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

- Owned auction snapshots while the auction house is open.
- Auction-created events followed by owned-auction snapshots.
- Auction-related mailbox rows when the mailbox is opened or updated.

Data is written to:

```text
World of Warcraft/_retail_/WTF/Account/<ACCOUNT>/SavedVariables/WowAuctionTracker.lua
```

WoW writes SavedVariables on `/reload`, logout, or game exit.

## Commands

- `/wat status`: print current captured row counts.
- `/wat scan`: query owned auctions and record a snapshot.
- `/wat mail`: scan visible inbox rows for auction-related mail.

## Notes

Mailbox outcome classification is conservative and English-client oriented.
Rows are stored with the raw mail subject and money/item fields so the Python
importer can improve classification later.
