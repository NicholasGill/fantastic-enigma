# WoW Auction Tracker Addon

Minimal Retail companion addon for exporting mailbox auction outcomes and
character gold snapshots into WoW SavedVariables.

## Install

Copy `addons/WowAuctionTracker` into your Retail addon directory:

```text
World of Warcraft/_retail_/Interface/AddOns/WowAuctionTracker
```

Enable `WoW Auction Tracker` in the in-game addon list, then log into the
character whose auctions you want to track.

## What It Records

- Auction-related mailbox rows when the mailbox is opened or updated. Repeated
  identical mailbox rows are skipped during the same login session.
- Character gold snapshots from `GetMoney()` on login, logout, money changes,
  manual `/wat gold` captures, and `/wat mail` captures.

Auction-house capture is disabled for now. The addon does not register
auction-house events, query owned auctions, or hook auction purchase APIs.

Data is written to:

```text
World of Warcraft/_retail_/WTF/Account/<ACCOUNT>/SavedVariables/WowAuctionTracker.lua
```

WoW writes SavedVariables on `/reload`, logout, or game exit.

## Commands

- `/wat status`: print current captured row counts.
- `/wat mail`: force-scan visible inbox rows for auction-related mail.
- `/wat gold`: force-record the current character gold balance.

## Notes

Mailbox outcome classification is conservative and English-client oriented.
Rows are stored with the raw mail subject and money/item fields so the Python
importer can improve classification later.

Auction-house tracking can be re-enabled later after the disconnect behavior is
understood.
