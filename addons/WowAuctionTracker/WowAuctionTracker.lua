local ADDON_NAME = ...
local WAT = CreateFrame("Frame")

local MAX_EVENTS = 5000
local DATA_VERSION = 3
local purchaseHooksInstalled = false
local pendingCommodityPurchase = nil

local function Now()
  return time()
end

local function PlayerName()
  local name, realm = UnitFullName("player")
  return name, realm or GetRealmName()
end

local function EnsureDB()
  if WowAuctionTrackerDB == nil then
    WowAuctionTrackerDB = {}
  end

  WowAuctionTrackerDB.version = DATA_VERSION
  WowAuctionTrackerDB.events = WowAuctionTrackerDB.events or {}
  WowAuctionTrackerDB.owned_snapshots = WowAuctionTrackerDB.owned_snapshots or {}
  WowAuctionTrackerDB.mail_events = WowAuctionTrackerDB.mail_events or {}
  WowAuctionTrackerDB.purchase_events = WowAuctionTrackerDB.purchase_events or {}
  WowAuctionTrackerDB.session = WowAuctionTrackerDB.session or {}
end

local function Append(tableName, row)
  EnsureDB()
  row.observed_at = row.observed_at or Now()
  row.source = ADDON_NAME

  table.insert(WowAuctionTrackerDB[tableName], row)
  while #WowAuctionTrackerDB[tableName] > MAX_EVENTS do
    table.remove(WowAuctionTrackerDB[tableName], 1)
  end
end

local function SimpleValue(value)
  local valueType = type(value)
  if valueType == "number" or valueType == "string" or valueType == "boolean" or value == nil then
    return value
  end
  return tostring(value)
end

local function RecordPurchaseEvent(eventType, row)
  EnsureDB()
  local character, realm = PlayerName()
  row = row or {}
  row.event_type = eventType
  row.character = row.character or character
  row.realm = row.realm or realm
  Append("purchase_events", row)
end

local function GetCommodityResultInfo(itemID, index)
  if C_AuctionHouse == nil or C_AuctionHouse.GetCommoditySearchResultInfo == nil then
    return nil
  end

  local ok, info = pcall(C_AuctionHouse.GetCommoditySearchResultInfo, itemID, index)
  if ok and info ~= nil then
    return info
  end

  ok, info = pcall(C_AuctionHouse.GetCommoditySearchResultInfo, index)
  if ok and info ~= nil then
    return info
  end

  return nil
end

local function EstimateCommodityPurchasePrice(itemID, quantity)
  if C_AuctionHouse == nil or C_AuctionHouse.GetNumCommoditySearchResults == nil then
    return nil, nil, nil
  end
  if itemID == nil or quantity == nil or quantity <= 0 then
    return nil, nil, nil
  end

  local ok, resultCount = pcall(C_AuctionHouse.GetNumCommoditySearchResults, itemID)
  if not ok or resultCount == nil or resultCount <= 0 then
    return nil, nil, nil
  end

  local remaining = quantity
  local totalPrice = 0
  for index = 1, resultCount do
    local info = GetCommodityResultInfo(itemID, index)
    if info ~= nil then
      local available = info.quantity or 0
      local unitPrice = info.unitPrice or info.buyoutAmount
      if available > 0 and unitPrice ~= nil then
        local purchased = math.min(remaining, available)
        totalPrice = totalPrice + (purchased * unitPrice)
        remaining = remaining - purchased
        if remaining <= 0 then
          return math.floor(totalPrice / quantity), totalPrice, "commodity_search_results_estimate"
        end
      end
    end
  end

  return nil, nil, nil
end

local function BuildCommodityPurchaseRow(eventType, itemID, quantity)
  local unitPrice, totalPrice, priceSource = EstimateCommodityPurchasePrice(itemID, quantity)
  if unitPrice == nil and pendingCommodityPurchase ~= nil and pendingCommodityPurchase.item_id == itemID then
    unitPrice = pendingCommodityPurchase.unit_price
    totalPrice = pendingCommodityPurchase.total_price
    priceSource = pendingCommodityPurchase.price_source
  end

  local row = {
    market = "commodity",
    item_id = itemID,
    quantity = quantity,
    unit_price = unitPrice,
    total_price = totalPrice,
    price_source = priceSource,
  }

  if eventType == "commodity_purchase_started" or eventType == "commodity_purchase_confirmed" then
    pendingCommodityPurchase = {
      item_id = itemID,
      quantity = quantity,
      unit_price = unitPrice,
      total_price = totalPrice,
      price_source = priceSource,
    }
  end

  return row
end

local function InstallPurchaseHooks()
  if purchaseHooksInstalled then
    return
  end
  if C_AuctionHouse == nil or hooksecurefunc == nil then
    return
  end

  if C_AuctionHouse.StartCommoditiesPurchase ~= nil then
    hooksecurefunc(C_AuctionHouse, "StartCommoditiesPurchase", function(itemID, quantity)
      RecordPurchaseEvent("commodity_purchase_started", BuildCommodityPurchaseRow("commodity_purchase_started", itemID, quantity))
    end)
  end

  if C_AuctionHouse.ConfirmCommoditiesPurchase ~= nil then
    hooksecurefunc(C_AuctionHouse, "ConfirmCommoditiesPurchase", function(itemID, quantity)
      RecordPurchaseEvent("commodity_purchase_confirmed", BuildCommodityPurchaseRow("commodity_purchase_confirmed", itemID, quantity))
    end)
  end

  if C_AuctionHouse.PlaceBid ~= nil then
    hooksecurefunc(C_AuctionHouse, "PlaceBid", function(auctionID, bidAmount)
      RecordPurchaseEvent("bid_or_buyout_placed", {
        market = "realm",
        auction_id = auctionID,
        total_price = bidAmount,
      })
    end)
  end

  purchaseHooksInstalled = true
end

local function MoneyFromOwnedAuction(info)
  if info == nil then
    return nil, nil
  end

  local unitPrice = info.unitPrice
  local buyoutAmount = info.buyoutAmount
  if unitPrice == nil and buyoutAmount ~= nil and info.quantity ~= nil and info.quantity > 0 then
    unitPrice = math.floor(buyoutAmount / info.quantity)
  end
  return unitPrice, buyoutAmount
end

local function ItemKeyToFields(itemKey)
  if itemKey == nil then
    return nil, nil, nil, nil
  end

  return itemKey.itemID, itemKey.itemLevel, itemKey.itemSuffix, itemKey.battlePetSpeciesID
end

local function RecordOwnedAuctionSnapshot(reason)
  EnsureDB()
  if C_AuctionHouse == nil or C_AuctionHouse.GetNumOwnedAuctions == nil then
    return
  end

  local character, realm = PlayerName()
  local snapshotId = string.format("%s-%d", character or "unknown", Now())
  local count = C_AuctionHouse.GetNumOwnedAuctions()

  for index = 1, count do
    local info = C_AuctionHouse.GetOwnedAuctionInfo(index)
    if info ~= nil then
      local unitPrice, buyoutAmount = MoneyFromOwnedAuction(info)
      local itemID, itemLevel, itemSuffix, battlePetSpeciesID = ItemKeyToFields(info.itemKey)
      Append("owned_snapshots", {
        snapshot_id = snapshotId,
        reason = reason,
        character = character,
        realm = realm,
        auction_id = info.auctionID,
        item_id = itemID,
        item_level = itemLevel,
        item_suffix = itemSuffix,
        battle_pet_species_id = battlePetSpeciesID,
        quantity = info.quantity,
        unit_price = unitPrice,
        buyout = buyoutAmount,
        bid_amount = info.bidAmount,
        bidder = info.bidder,
        time_left_seconds = info.timeLeftSeconds,
        status = info.status,
      })
    end
  end

  Append("events", {
    event_type = "owned_snapshot",
    reason = reason,
    character = character,
    realm = realm,
    snapshot_id = snapshotId,
    owned_auction_count = count,
  })
end

local function QueryOwnedAuctions()
  if C_AuctionHouse ~= nil and C_AuctionHouse.QueryOwnedAuctions ~= nil then
    C_AuctionHouse.QueryOwnedAuctions({{ sortOrder = 1, reverseSort = false }})
  end
end

local function LooksAuctionMail(subject)
  if subject == nil then
    return false
  end

  local text = string.lower(subject)
  return string.find(text, "auction", 1, true) ~= nil
end

local function OutcomeFromMail(subject, money, itemCount)
  if subject == nil then
    return "unknown"
  end

  local text = string.lower(subject)
  if money ~= nil and money > 0 and string.find(text, "auction", 1, true) ~= nil then
    return "sold"
  end
  if itemCount ~= nil and itemCount > 0 and string.find(text, "expired", 1, true) ~= nil then
    return "expired"
  end
  if itemCount ~= nil and itemCount > 0 and string.find(text, "cancel", 1, true) ~= nil then
    return "cancelled"
  end
  return "unknown"
end

local function RecordAuctionMail()
  EnsureDB()
  if GetInboxNumItems == nil then
    return
  end

  local character, realm = PlayerName()
  local numItems = GetInboxNumItems()

  for index = 1, numItems do
    local _, _, sender, subject, money, codAmount, daysLeft, itemCount, wasRead = GetInboxHeaderInfo(index)
    if LooksAuctionMail(subject) then
      local firstItemName, firstItemID, firstItemCount
      if itemCount ~= nil and itemCount > 0 and GetInboxItem ~= nil then
        firstItemName, firstItemID, _, firstItemCount = GetInboxItem(index, 1)
      end

      Append("mail_events", {
        character = character,
        realm = realm,
        mail_index = index,
        sender = sender,
        subject = subject,
        outcome = OutcomeFromMail(subject, money, itemCount),
        money = money,
        cod_amount = codAmount,
        days_left = daysLeft,
        item_count = itemCount,
        was_read = wasRead,
        first_item_name = firstItemName,
        first_item_id = firstItemID,
        first_item_count = firstItemCount,
      })
    end
  end
end

local function PrintStatus()
  EnsureDB()
  print(string.format(
    "WoW Auction Tracker v%d: %d owned rows, %d mail rows, %d purchase rows. SavedVariables update after /reload or logout.",
    WowAuctionTrackerDB.version or 0,
    #WowAuctionTrackerDB.owned_snapshots,
    #WowAuctionTrackerDB.mail_events,
    #WowAuctionTrackerDB.purchase_events
  ))
end

local function SafeRegisterEvent(eventName)
  pcall(function()
    WAT:RegisterEvent(eventName)
  end)
end

SLASH_WOWAUCTIONTRACKER1 = "/wat"
SLASH_WOWAUCTIONTRACKER2 = "/wowauctiontracker"
SlashCmdList.WOWAUCTIONTRACKER = function(command)
  command = string.lower(command or "")
  if command == "scan" then
    QueryOwnedAuctions()
    C_Timer.After(1, function()
      RecordOwnedAuctionSnapshot("slash_scan")
      PrintStatus()
    end)
  elseif command == "mail" then
    RecordAuctionMail()
    PrintStatus()
  elseif command == "status" or command == "" then
    PrintStatus()
  else
    print("WoW Auction Tracker commands: /wat scan, /wat mail, /wat status")
  end
end

SafeRegisterEvent("ADDON_LOADED")
SafeRegisterEvent("PLAYER_LOGIN")
SafeRegisterEvent("PLAYER_INTERACTION_MANAGER_FRAME_SHOW")
SafeRegisterEvent("AUCTION_HOUSE_SHOW")
SafeRegisterEvent("AUCTION_HOUSE_CLOSED")
SafeRegisterEvent("OWNED_AUCTIONS_UPDATED")
SafeRegisterEvent("AUCTION_HOUSE_AUCTION_CREATED")
SafeRegisterEvent("AUCTION_HOUSE_PURCHASE_COMPLETED")
SafeRegisterEvent("COMMODITY_PURCHASE_SUCCEEDED")
SafeRegisterEvent("COMMODITY_PURCHASE_FAILED")
SafeRegisterEvent("MAIL_SHOW")
SafeRegisterEvent("MAIL_INBOX_UPDATE")
WAT:SetScript("OnEvent", function(_, event, ...)
  if event == "ADDON_LOADED" and ... == ADDON_NAME then
    EnsureDB()
    InstallPurchaseHooks()
  elseif event == "PLAYER_LOGIN" then
    EnsureDB()
    InstallPurchaseHooks()
    local character, realm = PlayerName()
    WowAuctionTrackerDB.session.character = character
    WowAuctionTrackerDB.session.realm = realm
    WowAuctionTrackerDB.session.started_at = Now()
  elseif event == "PLAYER_INTERACTION_MANAGER_FRAME_SHOW" then
    local interactionType = ...
    if Enum ~= nil and Enum.PlayerInteractionType ~= nil and interactionType ~= Enum.PlayerInteractionType.Auctioneer then
      return
    end
    QueryOwnedAuctions()
    C_Timer.After(1, function()
      RecordOwnedAuctionSnapshot("auction_house_show")
    end)
  elseif event == "AUCTION_HOUSE_SHOW" then
    QueryOwnedAuctions()
    C_Timer.After(1, function()
      RecordOwnedAuctionSnapshot("auction_house_show")
    end)
  elseif event == "OWNED_AUCTIONS_UPDATED" then
    RecordOwnedAuctionSnapshot("owned_auctions_updated")
  elseif event == "AUCTION_HOUSE_AUCTION_CREATED" then
    Append("events", {
      event_type = "auction_created",
    })
    QueryOwnedAuctions()
    C_Timer.After(1, function()
      RecordOwnedAuctionSnapshot("auction_created")
    end)
  elseif event == "AUCTION_HOUSE_CLOSED" then
    RecordOwnedAuctionSnapshot("auction_house_closed")
  elseif event == "AUCTION_HOUSE_PURCHASE_COMPLETED" then
    local auctionID, itemID, quantity, totalPrice, unitPrice = ...
    if auctionID ~= nil and auctionID ~= 0 or itemID ~= nil or totalPrice ~= nil or unitPrice ~= nil then
      RecordPurchaseEvent("auction_purchase_completed", {
        market = "realm",
        auction_id = auctionID,
        item_id = itemID,
        quantity = quantity,
        total_price = totalPrice,
        unit_price = unitPrice,
        event_arg1 = SimpleValue(auctionID),
        event_arg2 = SimpleValue(itemID),
        event_arg3 = SimpleValue(quantity),
        event_arg4 = SimpleValue(totalPrice),
        event_arg5 = SimpleValue(unitPrice),
      })
    end
  elseif event == "COMMODITY_PURCHASE_SUCCEEDED" then
    local itemID, quantity, totalPrice, unitPrice = ...
    if itemID == nil and pendingCommodityPurchase ~= nil then
      itemID = pendingCommodityPurchase.item_id
    end
    if quantity == nil and pendingCommodityPurchase ~= nil then
      quantity = pendingCommodityPurchase.quantity
    end
    if totalPrice == nil and pendingCommodityPurchase ~= nil then
      totalPrice = pendingCommodityPurchase.total_price
    end
    if unitPrice == nil and pendingCommodityPurchase ~= nil then
      unitPrice = pendingCommodityPurchase.unit_price
    end
    RecordPurchaseEvent("commodity_purchase_succeeded", {
      market = "commodity",
      item_id = itemID,
      quantity = quantity,
      total_price = totalPrice,
      unit_price = unitPrice,
      price_source = pendingCommodityPurchase and pendingCommodityPurchase.price_source or nil,
      event_arg1 = SimpleValue(itemID),
      event_arg2 = SimpleValue(quantity),
      event_arg3 = SimpleValue(totalPrice),
      event_arg4 = SimpleValue(unitPrice),
    })
    pendingCommodityPurchase = nil
  elseif event == "COMMODITY_PURCHASE_FAILED" then
    local itemID, quantity = ...
    if itemID == nil and pendingCommodityPurchase ~= nil then
      itemID = pendingCommodityPurchase.item_id
    end
    if quantity == nil and pendingCommodityPurchase ~= nil then
      quantity = pendingCommodityPurchase.quantity
    end
    RecordPurchaseEvent("commodity_purchase_failed", {
      market = "commodity",
      item_id = itemID,
      quantity = quantity,
      event_arg1 = SimpleValue(itemID),
      event_arg2 = SimpleValue(quantity),
    })
    pendingCommodityPurchase = nil
  elseif event == "MAIL_SHOW" or event == "MAIL_INBOX_UPDATE" then
    RecordAuctionMail()
  end
end)
