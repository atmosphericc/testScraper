#!/usr/bin/env python3
"""
Pokemon Inventory — TCGPlayer Price Comparison Dashboard
Compares purchase prices in the CSV against real TCGPlayer market values.
Uses TCGCSV (tcgcsv.com) which mirrors the full TCGPlayer price database daily.

Port: 5003
Cache: config/pokemon_price_cache.json (24hr TTL)
"""

import csv
import json
import os
import re
import time
import threading
from collections import defaultdict
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)
app.secret_key = 'compare-inventory-2025'

CSV_PATH    = "Pokemon Inventory/Pokemon Inventory - Pokemon Cards.csv"
CACHE_PATH  = "config/pokemon_price_cache.json"
CACHE_TTL   = 86400   # 24 hours
TCGCSV_BASE = "https://tcgcsv.com/tcgplayer/3"

_refresh_lock  = threading.Lock()
_is_refreshing = False

HOLOFOIL_RARITIES = {
    'UR', 'Ultra Rare', 'Secret Rare', 'Hyper Rare', 'Ace Spec', 'SIR', 'IR',
    'Double Rare', 'Shiny Ultra Rare', 'Holo Rare', 'Shiny Holo Rare',
    'Shiny Rare', 'Amazing Rare', 'Cosmos Holo', 'Classic Collection', 'Promo',
    'Rainbow Rare', 'Gold Rare', 'Special Illustration Rare', 'Illustration Rare',
}

# ========== TCGCSV GROUP MAP ==========
# Maps CSV set names → TCGCSV group ID list.
# Source: https://tcgcsv.com/tcgplayer/3/groups  (Pokemon category = 3)
# Multiple IDs = combined fetch (main set + shiny vault / classic collection).

TCGCSV_GROUP_MAP = {
    # ── Scarlet & Violet ─────────────────────────────────────────────────────
    'Scarlet & Violet Base':              [22873],
    'Paldea Evolved':                     [23120],
    'Obsidian Flames':                    [23228],
    'Scarlet & Violet 151':               [23237],
    'Paradox Rift':                       [23286],
    'Paldean Fates':                      [23353],
    'Temporal Forces':                    [23381],
    'Twilight Masquerade':                [23473],
    'Stellar Crown':                      [23537],
    'Surging Sparks':                     [23651],
    'Prismatic Evolutions':               [23821],
    'Journey Together':                   [24073],
    'SV Promo':                           [22872],
    'Alternate Art Promo':                [1938],

    # ── Sword & Shield ───────────────────────────────────────────────────────
    'Sword & Shield Base':                [2585],
    'Rebel Clash':                        [2626],
    'Darkness Ablaze':                    [2675],
    "Champion's Path":                    [2685],
    'Vivid Voltage':                      [2701],
    'Shining Fates':                      [2754, 2781],  # main + Shiny Vault
    'Battle Styles':                      [2765],
    'Chilling Reign':                     [2807],
    'Evolving Skies':                     [2848],
    'Fusion Strike':                      [2906],
    'Fushion Strike':                     [2906],        # CSV typo
    'Brilliant Stars':                    [2948],
    'Brilliant Stars Trainer Gallery':    [3020],
    'Astral Radiance':                    [3040],
    'Astral Radiance Trainer Gallery':    [3068],
    'Lost Origin':                        [3118],
    'Lost Origin Trainer Gallery':        [3172],
    'Silver Tempest':                     [3170],
    'Silver Tempest Trainer Gallery':     [17674],
    'Crown Zenith':                       [17688],
    'Crown Zenith Galarian Gallery':      [17689],
    'Crown Zenith: Galarian Gallery':     [17689],
    'SWSH Promo':                         [2545],

    # ── Sun & Moon ───────────────────────────────────────────────────────────
    'Sun & Moon Base':                    [1863],
    'Guardians Rising':                   [1919],
    'Burning Shadows':                    [1957],
    'Shining Legends':                    [2054],
    'Crimson Invasion':                   [2071],
    'Ultra Prism':                        [2178],
    'Forbidden Light':                    [2209],
    'Celestial Storm':                    [2278],
    'Dragon Majesty':                     [2295],
    'Lost Thunder':                       [2328],
    'Team Up':                            [2377],
    'Unbroken Bonds':                     [2420],
    'Unified Minds':                      [2464],
    'Hidden Fates':                       [2480, 2594],  # main + Shiny Vault
    'Cosmic Eclipse':                     [2534],
    'SM Promo':                           [1861],

    # ── Celebrations ─────────────────────────────────────────────────────────
    'Celebrations':                       [2867, 2931],  # main + Classic Collection

    # ── XY ───────────────────────────────────────────────────────────────────
    'XY Base':                            [1387],
    'Flashfire':                          [1464],
    'Furious Fists':                      [1481],
    'Phantom Forces':                     [1494],
    'Primal Clash':                       [1509],
    'Roaring Skies':                      [1534],
    'Ancient Origins':                    [1576],
    'BREAKthrough':                       [1661],
    'XY - BREAKthrough':                  [1661],
    'BREAKpoint':                         [1701],
    'Fates Collide':                      [1780],
    'Steam Siege':                        [1815],
    'Evolutions':                         [1842],
    'XY Evolutions':                      [1842],
    'XY Promo':                           [1451],

    # ── Black & White ────────────────────────────────────────────────────────
    'Black & White Base':                 [1400],
    'Emerging Powers':                    [1424],
    'Noble Victories':                    [1403],
    'Next Destinies':                     [1404],
    'Dark Explorers':                     [1386],
    'Dragons Exalted':                    [1394],
    'Dragon Vault':                       [1426],
    'Boundaries Crossed':                 [1408],
    'Plasma Storm':                       [1383],
    'Plasma Freeze':                      [1384],
    'Plasma Blast':                       [1385],
    'Legendary Treasures':                [1409],
    'BW Promo':                           [1407],

    # ── HGSS / Call of Legends ───────────────────────────────────────────────
    'HeartGold & SoulSilver':             [1402],
    'Unleashed':                          [1388],
    'Undaunted':                          [1389],
    'Triumphant':                         [1390],
    'Call of Legends':                    [1415],
    'HGSS Promo':                         [1453],

    # ── Classic ──────────────────────────────────────────────────────────────
    'Base Set':                           [604],
    'Jungle':                             [635],
    'Fossil':                             [630],
    'Base Set 2':                         [605],
    'Team Rocket':                        [1440],

    # ── McDonald's ───────────────────────────────────────────────────────────
    'Mcdonalds 2024':                     [24163],
    "McDonald's 2024":                    [24163],
    'Mcdonalds 2021':                     [3150],
    "McDonald's 2021":                    [3150],

    # ── Other ────────────────────────────────────────────────────────────────
    'World Championship Decks':           [2282],
}

# Maps TCGCSV subTypeName → cache price key + priority (lower = preferred)
_SUBTYPE_KEY = {
    'Normal':                        ('normal',          1),
    'Holofoil':                      ('holofoil',        1),
    'Reverse Holofoil':              ('reverseHolofoil', 1),
    'Unlimited':                     ('normal',          2),
    'Unlimited Holofoil':            ('holofoil',        2),
    'Unlimited Reverse Holofoil':    ('reverseHolofoil', 2),
    '1st Edition':                   ('normal',          3),
    '1st Edition Holofoil':          ('holofoil',        3),
    '1st Edition Reverse Holofoil':  ('reverseHolofoil', 3),
}

_NOT_FOUND = {'found': False, 'normal': None, 'holofoil': None, 'reverseHolofoil': None}


# ========== NUMBER NORMALIZATION ==========

def normalize_number(number):
    """Strip '/total' — '25/102' → '25', 'TG01/TG30' → 'TG01'."""
    return number.split('/')[0] if '/' in number else number


def _alt_keys(raw_number):
    """
    Return all lookup keys for a TCGCSV card number so that mismatches
    between TCGCSV formatting and the user's CSV are bridged.

    Examples:
      SM168   → {SM168, 168}           SM prefix stripped
      SWSH077 → {SWSH077, 077, 77}     SWSH prefix + leading zeros stripped
      097     → {097, 97}              leading zeros stripped
      079a    → {079a, 79a}            leading zeros before letter suffix
    """
    number = normalize_number(raw_number)
    keys   = {number}

    # Strip letter-only prefix (SM, XY, SWSH, TG, GG, SV, …)
    m = re.match(r'^([A-Za-z]+)(\d+.*)$', number)
    if m:
        suffix = m.group(2)
        keys.add(suffix)
        stripped = re.sub(r'^0+(\d)', r'\1', suffix)
        if stripped != suffix:
            keys.add(stripped)

    # Strip leading zeros from the full number
    stripped_full = re.sub(r'^0+(\d)', r'\1', number)
    if stripped_full != number:
        keys.add(stripped_full)

    return keys


# ========== CSV ==========

def load_inventory():
    rows = []
    if not os.path.exists(CSV_PATH):
        return rows
    with open(CSV_PATH, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('Pokemon Name', '').strip()
            if not name:
                continue
            try:
                purchase = float(row.get('Purchase Price', '0') or '0')
            except ValueError:
                purchase = 0.0
            rows.append({
                'unique_id':      row.get('Unique ID', '').strip(),
                'name':           name,
                'number':         row.get('Number', '').strip(),
                'set':            row.get('Set', '').strip(),
                'rarity':         row.get('Rarity', '').strip(),
                'extra_filter':   row.get('Extra Filter', '').strip(),
                'condition':      row.get('Condition', '').strip(),
                'purchase_price': purchase,
                'order_id':       row.get('Order', '').strip(),
            })
    return rows


# ========== CACHE ==========

def load_cache():
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, 'r') as f:
            data = json.load(f)
        if time.time() - data.get('fetched_at', 0) > CACHE_TTL:
            return {}
        return data.get('prices', {})
    except Exception:
        return {}


def save_cache(prices_dict):
    os.makedirs('config', exist_ok=True)
    payload = {'fetched_at': time.time(), 'prices': prices_dict}
    temp    = CACHE_PATH + '.tmp'
    with open(temp, 'w') as f:
        json.dump(payload, f, indent=2)
    if os.path.exists(CACHE_PATH):
        os.remove(CACHE_PATH)
    os.rename(temp, CACHE_PATH)


# ========== API (TCGCSV) ==========

_session = requests.Session()
_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (compatible; PokemonInventoryDashboard/1.0)',
    'Accept':     'application/json',
})


def _fetch_set_prices(group_ids):
    """
    Download products + prices for one or more TCGCSV groups.
    Returns a dict: { lookup_key → {found, normal, holofoil, reverseHolofoil} }
    where lookup_key is any of the _alt_keys() variants for that card number.
    """
    # Step 1: productId → set of normalized lookup keys
    id_to_keys = {}
    for gid in group_ids:
        try:
            r = _session.get(f"{TCGCSV_BASE}/{gid}/products", timeout=20)
            if r.status_code != 200:
                print(f"[API]   products/{gid}: HTTP {r.status_code}")
                continue
            for p in r.json().get('results', []):
                ext     = {e['name']: e['value'] for e in p.get('extendedData', [])}
                raw_num = ext.get('Number', '')
                if raw_num:
                    id_to_keys[p['productId']] = _alt_keys(raw_num)
        except Exception as e:
            print(f"[API]   products/{gid} error: {e}")

    if not id_to_keys:
        return {}

    # Step 2: collect raw prices — {num_key: {price_key: (market, priority)}}
    raw = {}
    for gid in group_ids:
        try:
            r = _session.get(f"{TCGCSV_BASE}/{gid}/prices", timeout=20)
            if r.status_code != 200:
                continue
            for p in r.json().get('results', []):
                pid      = p.get('productId')
                keys     = id_to_keys.get(pid)
                if not keys:
                    continue
                subtype  = p.get('subTypeName', '')
                mapping  = _SUBTYPE_KEY.get(subtype)
                if not mapping:
                    continue
                price_key, priority = mapping
                market = p.get('marketPrice')
                if market is None:
                    continue
                market = float(market)
                # Store under every alt key for this product
                for k in keys:
                    if k not in raw:
                        raw[k] = {}
                    cur = raw[k].get(price_key)
                    if cur is None or priority < cur[1]:
                        raw[k][price_key] = (market, priority)
        except Exception as e:
            print(f"[API]   prices/{gid} error: {e}")

    # Step 3: flatten to {key: {found, normal, holofoil, reverseHolofoil}}
    table = {}
    for k, price_map in raw.items():
        table[k] = {
            'found':           True,
            'normal':          price_map['normal'][0]          if 'normal'          in price_map else None,
            'holofoil':        price_map['holofoil'][0]        if 'holofoil'        in price_map else None,
            'reverseHolofoil': price_map['reverseHolofoil'][0] if 'reverseHolofoil' in price_map else None,
        }
    return table


def refresh_all_prices(force=False):
    global _is_refreshing

    with _refresh_lock:
        if _is_refreshing:
            return False
        existing = load_cache()
        if existing and not force:
            return False
        _is_refreshing = True

    try:
        inventory = load_inventory()
        prices    = {}

        # Group inventory rows by set name
        rows_by_set = defaultdict(list)
        for row in inventory:
            rows_by_set[row['set']].append(row)

        total_sets  = len(rows_by_set)
        found_count = 0

        for set_idx, (set_name, rows) in enumerate(rows_by_set.items(), 1):
            group_ids = TCGCSV_GROUP_MAP.get(set_name.strip())
            print(f"[API] ({set_idx}/{total_sets}) {set_name!r} → groups {group_ids}")

            if not group_ids:
                # Set not in map — mark all its rows as not found
                for row in rows:
                    key = f"{row['name']}|{row['number']}|{row['set']}"
                    if key not in prices:
                        prices[key] = _NOT_FOUND
                continue

            # Fetch price table for this set (2 requests per group)
            price_table = _fetch_set_prices(group_ids)
            print(f"[API]   → {len(price_table)} price entries")

            # Match each inventory row by card number
            for row in rows:
                key     = f"{row['name']}|{row['number']}|{row['set']}"
                if key in prices:
                    continue  # duplicate row already handled
                # Try each alt-key for this row's number
                lookup_num = normalize_number(row['number'])
                result     = None
                for alt in _alt_keys(lookup_num):
                    if alt in price_table:
                        result = price_table[alt]
                        break
                if result is None:
                    result = _NOT_FOUND
                prices[key] = result
                if result.get('found'):
                    found_count += 1

        save_cache(prices)
        print(f"[CACHE] Saved {len(prices)} entries ({found_count} found) to {CACHE_PATH}")
        return True
    finally:
        with _refresh_lock:
            _is_refreshing = False


# ========== PRICE MATCHING ==========

def pick_market_price(cache_entry, rarity, extra_filter):
    if not cache_entry or not cache_entry.get('found'):
        return None

    ef     = (extra_filter or '').strip()
    r      = (rarity or '').strip()
    normal = cache_entry.get('normal')
    holo   = cache_entry.get('holofoil')
    rev    = cache_entry.get('reverseHolofoil')

    if ef == 'Reverse Holo':
        return rev if rev is not None else normal
    if r in HOLOFOIL_RARITIES:
        return holo if holo is not None else normal
    return normal


# ========== DATA ASSEMBLY ==========

def build_comparison_rows():
    inventory = load_inventory()
    cache     = load_cache()

    rows           = []
    total_purchase = 0.0
    total_market   = 0.0

    for row in inventory:
        key          = f"{row['name']}|{row['number']}|{row['set']}"
        cache_entry  = cache.get(key)
        market_price = pick_market_price(cache_entry, row['rarity'], row['extra_filter'])
        purchase     = row['purchase_price']
        total_purchase += purchase

        if market_price is not None:
            diff         = market_price - purchase
            diff_pct     = (diff / purchase * 100) if purchase else 0.0
            total_market += market_price
            found        = True
        else:
            diff = diff_pct = None
            found = False

        rows.append({
            'unique_id':      row['unique_id'],
            'name':           row['name'],
            'number':         row['number'],
            'set':            row['set'],
            'rarity':         row['rarity'],
            'extra_filter':   row['extra_filter'],
            'condition':      row['condition'],
            'purchase_price': purchase,
            'market_price':   market_price,
            'diff':           diff,
            'diff_pct':       diff_pct,
            'found':          found,
            'order_status':   row['order_id'],
        })

    def sort_key(r):
        if r['diff'] is None:
            return (2, 0)
        return (0 if r['diff'] >= 0 else 1, -(r['diff']))

    rows.sort(key=sort_key)
    for i, r in enumerate(rows):
        r['rank'] = i + 1

    total_diff  = total_market - total_purchase
    found_count = sum(1 for r in rows if r['found'])
    not_found   = sum(1 for r in rows if not r['found'])
    cache_fresh = bool(cache)

    summary = {
        'total_purchase':   round(total_purchase, 2),
        'total_market':     round(total_market, 2),
        'total_diff':       round(total_diff, 2),
        'total_diff_pct':   round(total_diff / total_purchase * 100, 1) if total_purchase else 0,
        'total_cards':      len(rows),
        'found_count':      found_count,
        'not_found_count':  not_found,
        'cache_fresh':      cache_fresh,
    }
    return rows, summary


# ========== RENDER ==========

def _render_rows(rows):
    if not rows:
        return '<tr><td colspan="10" class="empty-state">No inventory data found.</td></tr>'

    parts = []
    for r in rows:
        purchase_fmt = f"${r['purchase_price']:.2f}"

        if r['found'] and r['market_price'] is not None:
            market_fmt = f"${r['market_price']:.2f}"
        elif not r['found']:
            market_fmt = '<span class="not-found">Not Found</span>'
        else:
            market_fmt = '<span class="not-found">No Price</span>'

        if r['diff'] is not None:
            sign         = '+' if r['diff'] >= 0 else ''
            cls          = 'profit' if r['diff'] >= 0 else 'loss'
            row_cls      = 'row-profit' if r['diff'] >= 0 else 'row-loss'
            diff_fmt     = f'<span class="{cls}">{sign}${r["diff"]:.2f}</span>'
            diff_pct_fmt = f'<span class="{cls}">{sign}{r["diff_pct"]:.1f}%</span>'
        else:
            row_cls      = 'row-unknown'
            diff_fmt     = '<span class="neutral">&#8212;</span>'
            diff_pct_fmt = '<span class="neutral">&#8212;</span>'

        ef_badge = f'<span class="ef-badge">{r["extra_filter"]}</span>' if r['extra_filter'] else ''
        cond_cls = r['condition'].lower().replace(' ', '-') if r['condition'] else 'unknown'

        parts.append(f'''<tr class="card-row {row_cls}" data-search="{r['name'].lower()} {r['set'].lower()}">
            <td class="rank-cell">{r['rank']}</td>
            <td class="name-cell">{r['name']}{ef_badge}</td>
            <td class="set-cell">{r['set']}</td>
            <td class="mono">{r['number']}</td>
            <td><span class="cond-badge cond-{cond_cls}">{r['condition']}</span></td>
            <td class="rarity-cell">{r['rarity']}</td>
            <td class="mono price-cell">{purchase_fmt}</td>
            <td class="mono price-cell">{market_fmt}</td>
            <td class="mono">{diff_fmt}</td>
            <td class="mono">{diff_pct_fmt}</td>
        </tr>''')
    return ''.join(parts)


# ========== ROUTES ==========

@app.route('/')
def index():
    rows, summary = build_comparison_rows()
    table_html    = _render_rows(rows)

    diff_color = '#4caf50' if summary['total_diff'] >= 0 else '#f44336'
    diff_sign  = '+' if summary['total_diff'] >= 0 else ''
    cache_note = '' if summary['cache_fresh'] else '<span class="no-cache-badge">&#9888; No price data &mdash; click Refresh Prices</span>'

    HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pokemon Inventory &#8212; Price Comparison</title>
<style>
:root {
    --primary-bg:    #0f1117;
    --secondary-bg:  #1a1d2e;
    --accent-color:  #6c63ff;
    --success-color: #4caf50;
    --danger-color:  #f44336;
    --text-primary:  #e8eaf6;
    --text-secondary:#9e9e9e;
    --text-muted:    #616161;
    --border-color:  #2a2d3e;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    background: var(--primary-bg);
    color: var(--text-primary);
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 14px;
    min-height: 100vh;
    padding: 2rem;
}
h1 { font-size: 1.6rem; font-weight: 700; margin-bottom: 0.25rem; }
.subtitle { color: var(--text-muted); font-size: 0.82rem; margin-bottom: 2rem; }
.summary-bar { display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
.stat-card {
    background: var(--secondary-bg);
    border: 1px solid var(--border-color);
    border-radius: 10px;
    padding: 1rem 1.5rem;
    min-width: 180px;
}
.stat-label { font-size: 0.75rem; color: var(--text-muted); margin-bottom: 0.3rem; }
.stat-value { font-size: 1.4rem; font-weight: 700; font-family: 'Courier New', monospace; }
.stat-sub   { font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.2rem; }
.toolbar { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; flex-wrap: wrap; }
.search-input {
    padding: 0.6rem 1rem;
    background: var(--secondary-bg);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    color: var(--text-primary);
    font-size: 0.875rem;
    width: 280px;
    outline: none;
}
.search-input:focus { border-color: var(--accent-color); }
.btn { padding: 0.6rem 1.2rem; border: none; border-radius: 6px; cursor: pointer; font-size: 0.82rem; font-weight: 600; transition: opacity 0.2s; white-space: nowrap; }
.btn-primary { background: var(--accent-color); color: white; }
.btn-primary:hover { opacity: 0.85; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.refresh-status { font-size: 0.8rem; color: var(--text-secondary); display: none; }
.no-cache-badge { font-size: 0.8rem; background: rgba(255,152,0,0.15); border: 1px solid rgba(255,152,0,0.4); color: #ff9800; padding: 0.3rem 0.7rem; border-radius: 4px; }
.filter-info { font-size: 0.8rem; color: var(--text-muted); margin-left: auto; }
.table-wrap { background: var(--secondary-bg); border: 1px solid var(--border-color); border-radius: 10px; overflow: hidden; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
thead th { background: #12151f; padding: 0.75rem 0.9rem; text-align: left; font-size: 0.72rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--border-color); white-space: nowrap; }
.card-row td { padding: 0.7rem 0.9rem; border-bottom: 1px solid var(--border-color); font-size: 0.85rem; vertical-align: middle; }
.card-row:last-child td { border-bottom: none; }
.card-row:hover td { background: rgba(255,255,255,0.03); }
.row-profit td { background: rgba(76,175,80,0.05); }
.row-loss   td { background: rgba(244,67,54,0.05); }
.row-unknown td { opacity: 0.7; }
.rank-cell   { color: var(--text-muted); font-size: 0.75rem; width: 40px; }
.name-cell   { font-weight: 600; max-width: 200px; }
.set-cell    { color: var(--text-secondary); max-width: 160px; font-size: 0.82rem; }
.rarity-cell { font-size: 0.8rem; color: var(--text-secondary); }
.price-cell  { color: var(--text-primary); }
.mono        { font-family: 'Courier New', monospace; }
.profit   { color: #4caf50; font-weight: 600; }
.loss     { color: #f44336; font-weight: 600; }
.neutral  { color: var(--text-muted); }
.not-found{ color: var(--text-muted); font-style: italic; font-size: 0.8rem; }
.ef-badge { display: inline-block; font-size: 0.65rem; background: rgba(108,99,255,0.2); border: 1px solid rgba(108,99,255,0.4); color: #a09cf7; border-radius: 3px; padding: 1px 5px; margin-left: 5px; vertical-align: middle; }
.cond-badge { display: inline-block; font-size: 0.7rem; font-weight: 600; padding: 2px 6px; border-radius: 4px; background: var(--border-color); color: var(--text-secondary); }
.cond-nm       { background: rgba(76,175,80,0.2);  color: #4caf50; }
.cond-lp       { background: rgba(139,195,74,0.2); color: #8bc34a; }
.cond-mp       { background: rgba(255,152,0,0.2);  color: #ff9800; }
.cond-hp       { background: rgba(255,87,34,0.2);  color: #ff5722; }
.cond-damaged  { background: rgba(244,67,54,0.2);  color: #f44336; }
.empty-state { text-align: center; padding: 3rem; color: var(--text-muted); font-style: italic; }
#toast { position: fixed; bottom: 1.5rem; right: 1.5rem; padding: 0.75rem 1.25rem; border-radius: 8px; color: white; font-size: 0.875rem; font-weight: 500; opacity: 0; transition: opacity 0.3s; z-index: 1000; pointer-events: none; }
#toast.show    { opacity: 1; }
#toast.success { background: var(--success-color); }
#toast.error   { background: var(--danger-color); }
#toast.warning { background: #ff9800; }
</style>
</head>
<body>
<h1>Pokemon Inventory</h1>
<p class="subtitle">Port 5003 &mdash; TCGPlayer market prices via TCGCSV (updated daily)</p>

<div class="summary-bar">
    <div class="stat-card">
        <div class="stat-label">Total Cards</div>
        <div class="stat-value">''' + str(summary['total_cards']) + '''</div>
        <div class="stat-sub">''' + str(summary['found_count']) + ''' priced &middot; ''' + str(summary['not_found_count']) + ''' not found</div>
    </div>
    <div class="stat-card">
        <div class="stat-label">Total Purchase Cost</div>
        <div class="stat-value">$''' + f"{summary['total_purchase']:.2f}" + '''</div>
        <div class="stat-sub">from CSV</div>
    </div>
    <div class="stat-card">
        <div class="stat-label">Total Market Value</div>
        <div class="stat-value">$''' + f"{summary['total_market']:.2f}" + '''</div>
        <div class="stat-sub">TCGPlayer market prices</div>
    </div>
    <div class="stat-card" style="border-color: ''' + diff_color + '''33;">
        <div class="stat-label">Total Gain / Loss</div>
        <div class="stat-value" style="color: ''' + diff_color + ''';">''' + diff_sign + '''$''' + f"{abs(summary['total_diff']):.2f}" + '''</div>
        <div class="stat-sub" style="color: ''' + diff_color + ''';">''' + diff_sign + str(summary['total_diff_pct']) + '''% vs purchase cost</div>
    </div>
</div>

<div class="toolbar">
    <input class="search-input" type="text" placeholder="Search by card name or set..."
        oninput="filterTable(this.value)" id="search-box">
    <button class="btn btn-primary" id="refresh-btn" onclick="startRefresh()">
        Refresh Prices
    </button>
    <span class="refresh-status" id="refresh-status">
        Fetching prices<span id="dots">...</span> (~25 sec)
    </span>
    ''' + cache_note + '''
    <span class="filter-info" id="filter-info">''' + str(summary['total_cards']) + ''' cards</span>
</div>

<div class="table-wrap">
    <table>
        <thead>
            <tr>
                <th>#</th>
                <th>Card Name</th>
                <th>Set</th>
                <th>No.</th>
                <th>Cond.</th>
                <th>Rarity</th>
                <th>Purchase $</th>
                <th>Market $</th>
                <th>Diff $</th>
                <th>Diff %</th>
            </tr>
        </thead>
        <tbody id="card-tbody">
            ''' + table_html + '''
        </tbody>
    </table>
</div>

<div id="toast"></div>

<script>
function filterTable(q) {
    const lower = q.toLowerCase();
    const rows  = document.querySelectorAll('.card-row');
    let visible = 0;
    rows.forEach(row => {
        const match = row.getAttribute('data-search').includes(lower);
        row.style.display = match ? '' : 'none';
        if (match) visible++;
    });
    const total = document.querySelectorAll('.card-row').length;
    document.getElementById('filter-info').textContent =
        q ? (visible + ' of ' + total + ' cards') : (total + ' cards');
}

function showToast(msg, type) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className   = 'show ' + type;
    clearTimeout(t._timer);
    t._timer = setTimeout(() => t.className = '', 3500);
}

async function startRefresh() {
    const btn    = document.getElementById('refresh-btn');
    const status = document.getElementById('refresh-status');
    btn.disabled    = true;
    btn.textContent = 'Refreshing...';
    status.style.display = 'inline';

    try {
        const res  = await fetch('/refresh-prices', {method: 'POST'});
        const data = await res.json();
        if (data.started) {
            showToast('Price refresh started (~25 seconds)', 'warning');
            pollStatus();
        } else {
            showToast(data.message || 'Already refreshing', 'warning');
            pollStatus();
        }
    } catch(e) {
        showToast('Network error: ' + e.message, 'error');
        btn.disabled    = false;
        btn.textContent = 'Refresh Prices';
        status.style.display = 'none';
    }
}

function pollStatus() {
    let dots = 0;
    const interval = setInterval(async () => {
        dots = (dots % 3) + 1;
        document.getElementById('dots').textContent = '.'.repeat(dots);
        try {
            const res  = await fetch('/refresh-status');
            const data = await res.json();
            if (!data.is_refreshing) {
                clearInterval(interval);
                showToast('Prices updated! Reloading...', 'success');
                setTimeout(() => window.location.reload(), 1000);
            }
        } catch(e) { clearInterval(interval); }
    }, 2000);
}

(async () => {
    const res  = await fetch('/refresh-status');
    const data = await res.json();
    if (data.is_refreshing) {
        document.getElementById('refresh-btn').disabled    = true;
        document.getElementById('refresh-btn').textContent = 'Refreshing...';
        document.getElementById('refresh-status').style.display = 'inline';
        pollStatus();
    }
})();
</script>
</body>
</html>'''
    return HTML


@app.route('/refresh-prices', methods=['POST'])
def refresh_prices():
    global _is_refreshing
    if _is_refreshing:
        return jsonify({'started': False, 'message': 'Refresh already in progress'})
    t = threading.Thread(target=lambda: refresh_all_prices(force=True), daemon=True)
    t.start()
    return jsonify({'started': True, 'message': 'Price refresh started (~25 seconds)'})


@app.route('/refresh-status')
def refresh_status():
    return jsonify({
        'is_refreshing': _is_refreshing,
        'cache_exists':  os.path.exists(CACHE_PATH),
    })


# ========== MAIN ==========

if __name__ == '__main__':
    print("=" * 60)
    print("  Pokemon Inventory — Price Comparison Dashboard")
    print("  Port: 5003  |  Prices: TCGCSV (tcgcsv.com)")
    print(f"  CSV:   {CSV_PATH}")
    print(f"  Cache: {CACHE_PATH}  (24hr TTL)")
    print("=" * 60)
    print("  Open: http://127.0.0.1:5003")
    print("=" * 60)

    print("[STARTUP] Testing TCGCSV connectivity...")
    try:
        t0 = time.time()
        r  = _session.get(f"{TCGCSV_BASE}/groups", timeout=10)
        r.raise_for_status()
        n_groups = r.json().get('totalItems', '?')
        print(f"[STARTUP] TCGCSV reachable — {n_groups} groups in {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"[STARTUP] WARNING: TCGCSV test failed: {e}")

    if not os.path.exists(CACHE_PATH):
        print("[STARTUP] No cache found — fetching prices in background (~25 sec)")
        t = threading.Thread(target=lambda: refresh_all_prices(force=False), daemon=True)
        t.start()

    from waitress import serve
    serve(app, host='127.0.0.1', port=5003, threads=4, channel_timeout=120)
