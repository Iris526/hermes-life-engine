# LifeEngine v0.12.8 — Outfit Resolver / Current Outfit / Action Chain Closure

## Purpose

v0.12.8 closes the gap between “having collections” and “using items in life actions”. The Agent no longer merely owns wardrobe/shoe/vanity/accessory items; it can resolve a styling request, check whether assets are complete, wear an outfit, return it, and run a purchase → intake → asset-generation → usable-candidate chain.

## Event / Schedule / Item Principle

- Event = thing/intention/lifecycle.
- ScheduleBlock = concrete time reservation for an Event.
- Collection Item = concrete owned object or reusable styling recipe.
- OutfitResolution = resolver output from natural request to item refs.
- OutfitSnapshot = current wearing state.

An Agent should never conjure an outfit from prose alone. It must resolve items from collections. Missing shoes/accessories/vanity/socks are explicit resolver output.

## New tables

- outfit_resolutions
- outfit_snapshots
- collection_asset_checks
- collection_purchase_chains
- stale_event_cleanup_runs

## New collection actions

- resolve_outfit
- current_outfit
- outfit_snapshots
- wear_outfit
- return_outfit
- asset_check
- purchase_chain
- purchase_chains

## Outfit Resolver

Given a request like “穿浅蓝那套”, LifeEngine resolves:

- wardrobe item
- sock layer or bare_legs state
- shoe item
- accessory items
- vanity makeup/hairstyle recipe
- asset completeness for every selected item

Output includes missing items and needs_generation records. Missing collections do not invent items.

## Outfit Snapshot

Records current worn state:

- outfit_plan_id
- event_id
- refs_json
- worn_at / removed_at
- dirty_state_json
- asset_completeness_json

Wearing an outfit checks out items. Returning it returns items and marks cleanliness state.

## Asset completeness

Before final image generation or visual use, LifeEngine can check whether every selected item has the required asset views fulfilled. If not, the item is marked needs_generation.

## Purchase → Intake → Wear chain

A clothing/shoe/accessory purchase can now perform:

1. optional resource delta, e.g. money.lingzhu cost
2. item intake to the correct collection
3. asset generation job creation
4. purchase chain record
5. future outfit candidate availability

## Stale event cleanup

Adds a safe cleanup surface for old schedule blocks and planned events so autonomy is not blocked forever by stale abstract goal placeholders.

## Agent-facing policy

- To dress: use life_collection(action="resolve_outfit") first.
- To render or generate worn images: use asset_check first.
- To actually wear: use wear_outfit.
- To finish an outing: use return_outfit.
- To buy new wearable items: use purchase_chain.
- To clean old planned events: use life_schedule(action="cleanup_stale").
