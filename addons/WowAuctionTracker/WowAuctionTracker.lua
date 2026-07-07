local ADDON_NAME = ...
local WAT = CreateFrame("Frame")

local MAX_EVENTS = 5000
local DATA_VERSION = 6
local mailScanScheduled = false
local pendingMailScanReason = nil
local pendingMailScanForce = false

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
  WowAuctionTrackerDB.gold_snapshots = WowAuctionTrackerDB.gold_snapshots or {}
  WowAuctionTrackerDB.session = WowAuctionTrackerDB.session or {}
  WowAuctionTrackerDB.session.seen = WowAuctionTrackerDB.session.seen or {}
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

local function KeyPart(value)
  if value == nil then
    return ""
  end
  return string.gsub(tostring(value), "\31", " ")
end

local function BuildKey(...)
  local parts = {}
  for index = 1, select("#", ...) do
    parts[index] = KeyPart(select(index, ...))
  end
  return table.concat(parts, "\31")
end

local function SeenNamespace(namespace)
  EnsureDB()
  WowAuctionTrackerDB.session.seen[namespace] = WowAuctionTrackerDB.session.seen[namespace] or {}
  return WowAuctionTrackerDB.session.seen[namespace]
end

local function AppendUnique(tableName, row, namespace, key)
  if key ~= nil then
    local seen = SeenNamespace(namespace)
    if seen[key] then
      return false
    end
    seen[key] = true
  end
  Append(tableName, row)
  return true
end

local function RecordGoldSnapshot(reason)
  EnsureDB()
  if GetMoney == nil then
    return
  end

  local character, realm = PlayerName()
  Append("gold_snapshots", {
    reason = reason,
    character = character,
    realm = realm,
    money = GetMoney(),
  })
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

local function MailRowKey(row)
  return BuildKey(
    row.character,
    row.realm,
    row.sender,
    row.subject,
    row.outcome,
    row.money,
    row.cod_amount,
    row.item_count,
    row.first_item_name,
    row.first_item_id,
    row.first_item_count
  )
end

local function RecordAuctionMail(reason, force)
  EnsureDB()
  if GetInboxNumItems == nil then
    return
  end

  local character, realm = PlayerName()
  local numItems = GetInboxNumItems()
  local recordedCount = 0

  for index = 1, numItems do
    local _, _, sender, subject, money, codAmount, daysLeft, itemCount, wasRead = GetInboxHeaderInfo(index)
    if LooksAuctionMail(subject) then
      local firstItemName, firstItemID, firstItemCount
      if itemCount ~= nil and itemCount > 0 and GetInboxItem ~= nil then
        firstItemName, firstItemID, _, firstItemCount = GetInboxItem(index, 1)
      end

      local row = {
        reason = reason,
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
      }
      if force then
        Append("mail_events", row)
        recordedCount = recordedCount + 1
      elseif AppendUnique("mail_events", row, "mail_events", MailRowKey(row)) then
        recordedCount = recordedCount + 1
      end
    end
  end

  Append("events", {
    event_type = "mail_scan",
    reason = reason,
    character = character,
    realm = realm,
    inbox_count = numItems,
    recorded_mail_count = recordedCount,
  })
end

local function ScheduleAuctionMailScan(reason, delaySeconds, force)
  pendingMailScanReason = reason or pendingMailScanReason
  pendingMailScanForce = pendingMailScanForce or force == true

  if mailScanScheduled then
    return
  end

  mailScanScheduled = true
  local function scan()
    local scanReason = pendingMailScanReason or reason
    local scanForce = pendingMailScanForce
    mailScanScheduled = false
    pendingMailScanReason = nil
    pendingMailScanForce = false
    RecordAuctionMail(scanReason, scanForce)
  end

  if C_Timer ~= nil and C_Timer.After ~= nil then
    C_Timer.After(delaySeconds or 0.5, scan)
  else
    scan()
  end
end

local function PrintStatus()
  EnsureDB()
  print(string.format(
    "WoW Auction Tracker v%d: %d mail rows, %d gold rows. Auction-house capture is disabled. SavedVariables update after /reload or logout.",
    WowAuctionTrackerDB.version or 0,
    #WowAuctionTrackerDB.mail_events,
    #WowAuctionTrackerDB.gold_snapshots
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
    RecordGoldSnapshot("slash_scan")
    print("WoW Auction Tracker: auction-house scan is disabled; recorded gold only.")
    PrintStatus()
  elseif command == "mail" then
    RecordGoldSnapshot("slash_mail")
    RecordAuctionMail("slash_mail", true)
    PrintStatus()
  elseif command == "gold" then
    RecordGoldSnapshot("slash_gold")
    PrintStatus()
  elseif command == "status" or command == "" then
    RecordGoldSnapshot("slash_status")
    PrintStatus()
  else
    print("WoW Auction Tracker commands: /wat mail, /wat gold, /wat status")
  end
end

SafeRegisterEvent("ADDON_LOADED")
SafeRegisterEvent("PLAYER_LOGIN")
SafeRegisterEvent("PLAYER_LOGOUT")
SafeRegisterEvent("PLAYER_MONEY")
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
    WowAuctionTrackerDB.session.seen = {}
    RecordGoldSnapshot("player_login")
  elseif event == "PLAYER_LOGOUT" then
    RecordGoldSnapshot("player_logout")
  elseif event == "PLAYER_MONEY" then
    RecordGoldSnapshot("player_money")
  elseif event == "MAIL_SHOW" then
    ScheduleAuctionMailScan("mail_show", 0.5, false)
  elseif event == "MAIL_INBOX_UPDATE" then
    ScheduleAuctionMailScan("mail_inbox_update", 0.5, false)
  end
end)
