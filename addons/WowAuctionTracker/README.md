# WoW Auction Tracker Addon

Minimal Retail companion addon for exporting your own auction activity into
WoW SavedVariables.

## Install

Copy `addons/WowAuctionTracker` into your Retail addon directory:

```text
World of Warcraft/_retail_/Interface/AddOns/WowAuctionTracker
```

Enable `WoW Auction Tracker` in the in-game addon list, then log into the
character whose auctions you want to track.

## What It Records

- Event-driven owned auction snapshots when the auction house opens, owned
  auctions update, auctions are created, or the auction house closes. Repeated
  identical owned-auction rows are skipped during the same login session.
- Best-effort owned-auction details such as posted unit price, stack size,
  deposit cost, and auction duration when the WoW API exposes them.
- Auction-created events followed by owned-auction snapshots.
- Auction-related mailbox rows when the mailbox is opened or updated. Repeated
  identical mailbox rows are skipped during the same login session.
- Auction purchase events and purchase intents when the auction house purchase
  APIs fire. Commodity purchases include a best-effort price estimate from the
  visible commodity search results when WoW does not include price fields in the
  purchase success event.

Data is written to:

```text
World of Warcraft/_retail_/WTF/Account/<ACCOUNT>/SavedVariables/WowAuctionTracker.lua
```

WoW writes SavedVariables on `/reload`, logout, or game exit.

## Commands

- `/wat status`: print current captured row counts.
- `/wat scan`: query owned auctions and force-record the visible snapshot.
- `/wat mail`: force-scan visible inbox rows for auction-related mail.

## Notes

Mailbox outcome classification is conservative and English-client oriented.
Rows are stored with the raw mail subject and money/item fields so the Python
importer can improve classification later.

Purchase tracking is best effort because Blizzard's auction events do not
always include the same item and price fields for every purchase path. The addon
stores raw event fields alongside normalized fields when available.
