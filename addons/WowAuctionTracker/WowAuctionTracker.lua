local ADDON_NAME = ...
local WAT = CreateFrame("Frame")

local MAX_EVENTS = 5000

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

  WowAuctionTrackerDB.version = 1
  WowAuctionTrackerDB.events = WowAuctionTrackerDB.events or {}
  WowAuctionTrackerDB.owned_snapshots = WowAuctionTrackerDB.owned_snapshots or {}
  WowAuctionTrackerDB.mail_events = WowAuctionTrackerDB.mail_events or {}
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
    "WoW Auction Tracker: %d owned rows, %d mail rows. SavedVariables update after /reload or logout.",
    #WowAuctionTrackerDB.owned_snapshots,
    #WowAuctionTrackerDB.mail_events
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
SafeRegisterEvent("MAIL_SHOW")
SafeRegisterEvent("MAIL_INBOX_UPDATE")
WAT:SetScript("OnEvent", function(_, event, ...)
  if event == "ADDON_LOADED" and ... == ADDON_NAME then
    EnsureDB()
  elseif event == "PLAYER_LOGIN" then
    EnsureDB()
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
  elseif event == "MAIL_SHOW" or event == "MAIL_INBOX_UPDATE" then
    RecordAuctionMail()
  end
end)
