"""
Walmart stock/price checker using the Orchestra GraphQL API.
Uses the exact headers and POST body captured from the browser.

Usage:
  python walmart/stock_check.py
"""

import requests
import json
import concurrent.futures

WALMART_SELLER_ID = "F55CDC31AB754BB68FE0851F0F1F2C96"

GRAPHQL_HASH = "20d116c298a901b29763c37a4aaf8b37aeb1654e4f971cd11a7fe9de2ceab027"

COOKIES_RAW = "vtc=VLzeDQT-RIDVPuXx7VntRU; bstc=VLzeDQT-RIDVPuXx7VntRU; _pxvid=8dadb49b-2490-11f1-86b4-8cb0fa86473b; pxcts=8e71491b-2490-11f1-863e-14f26e1be02a; AID=wmlspartner=0:reflectorid=0000000000000000000000:lastupd=1774033947073; _intlbu=false; _m=9; _shcc=US; hasLocData=1; userAppVersion=usweb-1.251.0-c88ea9c8137ed74a73df2e810aff797c113bc48a-3192043r; abqme=true; xpth=x-o-mart%2BB2C~x-o-mverified%2Bfalse; isoLoc=US_CO_t3; _astc=eb4df3a8e33948c1a5d2d9a14bd8f0af; assortmentStoreId=2815; bm_mi=1CC8BE5FDBE0C6E0BB6B359AB9ECCE93~YAAQj1HDF8AymQOdAQAAqoqqDB+5Mu8U+CxxaOySMj9GR+g7Cx27d2f0LahmrnTY7BXEbPnDQjTWItNFI4EKQEo40KG5pl4X4uIFGlHSqoHq86+uTI38m9rVOSDRy/SAFa0tMF/zOG5/3NkGAYT4N11o8CSBCqKoEHt63XY+nXgnp4o5CxiySxT3nploEeizl+EFcuu9k8p/iltCd3Xlb5hHH6NZn77eSGldIIBlq/hEq7w+oQvocBe31/tThKgwtbuXDIiZEeLuTZDJ0o6NJr9HTLL1YAD0cxCz11K6S8aH+Ak1RMZAuoDeUVfcYkq8cl+s1tk=~1; bm_sv=731111C441BEE98D07B9055268EB8CE4~YAAQj1HDF2Y1mQOdAQAAeY+qDB/FxBsbavRR7eshSOfgLZHu2E08NP6sBk+Koh84kWOqpUkQjSj24U9aFzCkH+zw6rfNBo1jSB0aS2IuLNVB6CN+Sh/TpekYtf5V0qUhUrGlTqVYvN8m1IH5t/d1CmktO5O/pf6WL2Sv4O0WYg2qdotdfqNur/J1d3GQPUE73eC+RhaUkP0DDXgFo2sy7nDj4bkedRQ+Yb4EQjywKgGPrzKJtUI8gr9poKI2YGr7zyQ=~1; ak_bmsc=1CDCED5E562441298C24DCB712648061~000000000000000000000000000000~YAAQjFHDF+EAdQedAQAAThGrDB9YimBKgKpI8ms9432WTY4ON+4gSE5kDoZDYgrpjXU7IcxHXI1cfKchNkQaf5BO7KY9FerJ3rgnt+HjoD3dZbodc3KeRYc8vqPJnqRmEBkcwEjvUvTW9aL/R4TecIqBGyZ4o1SjEdx3cHBlJfKGqF2SKtgzU7ee64nYedHGfzWhSMeNMqUd+yaEj3y8lEnv2QV6v0Ng9o7K1zWFCm5K9TEIwwJSlvF73FNum4MUx9sZOAmagUR5G/5gsJHEbwEVa4BSEjFlr0fi01ICU7J9H8sqf+8O85FIVc1Di+av3IoldoIaMH8WyKxNFxkNlVfMjL2XM8lnK7b0OLJiE2xPs41kEF86HPIgFtTNRzp55Nv/lxCB7w+GGPRE5LEqjW1mDC5aybJCvmQQ5+TVz/0ZQsgEm3lxWEg=; io_id=aff19b5e-6d4b-4e71-b03b-4e90e4468b0d; TS012af430=01ada83fb3841d55883d7852bf2b98c381d287eb04b12b87215a30ab81a98355d9119ac151766baa879245b980a40c698230458f2a; CID=6b724790-e048-40b3-ae25-55d0106d3ce3; SPID=MDYyMTYyMDI1U9GcxLdntNzluueVZYFGVF9mu4x_wZGCRZHTFJBAcHPOhQY8cQO6xtr7buJrH-vFnRJXLJzrN3F-0AZ_lAb0VIZqww6kQQPfwxJAJA; _vc=Wp09Mwg8gfsOUiCjfp%2FHNhCsl7G9LUsRGpxrnujk8h4%3D; customer=%7B%22firstName%22%3A%22Eric%22%2C%22lastNameInitial%22%3A%22P%22%2C%22ceid%22%3A%2218970f8c4873383eb4a725f4c4eaef71400ef1718be11e00346e1573c23e45c6%22%7D; hasCID=1; type=REGISTERED; wm_accept_language=en-US; _s=Fz90B%2Ce39Iz%2CzIgNX%3A1774034137987; _sc=9u7SyVxzSAN07wgOda%2BLcmoG3lX9d64q9cG82uUIo%2Fo%3D; _tpl=80; _tplc=gG4gMe8MgCC8+gSgkSSMaYVaC5Rp1fbvVt+Ya+tJcDs=; xpa=-5-yD|-cmOw|0lZ3h|0nPS5|23qMA|2N70j|3fiuz|45qL0|5uGe4|6iCSl|7r5xW|7z1cs|8EYrX|9-Mgs|APy6f|CKYt9|ELLRd|FBNDv|Fwpvl|G3C5M|H9OCD|IoDQX|J996E|KOkFd|MBrEo|NKQqc|OOxSI|O_ZeO|PlswE|VV_ZT|VrNQh|VxBQH|Xoi-P|Ys7mu|YunQ9|aSd_h|aogFV|bICs1|c75G7|c8xTZ|dPQHY|fdm-7|h4xs9|jKBKo|jM1ax|jPq86|mTunu|m_KDC|nDGnS|nKPp0|pE3xR|pOoUG|pbsbu|rFvdv|sXZZx|sgN12|tNjwU|tXSmL|yIL-0|yyVxL|zFzrA|zR-nl; exp-ck=-cmOw10nPS512N70j345qL016iCSl17r5xW27z1cs18EYrX6APy6f1CKYt91ELLRd1Fwpvl1H9OCD7J996E1KOkFd1MBrEo8OOxSI1O_ZeO3PlswE2VxBQH1YunQ91aSd_h5aogFV1bICs11c8xTZ1fdm-71jPq867mTunu1nDGnS1nKPp01pE3xR1pbsbu2rFvdv1sXZZx1tNjwU1tXSmL1yIL-05zFzrA1; xptc=_m%2B9~_s%2BFz90B%2Ce39Iz%2CzIgNX%3A1774034137987~assortmentStoreId%2B2815; xpm=1%2B1774034140%2BVLzeDQT-RIDVPuXx7VntRU~6b724790-e048-40b3-ae25-55d0106d3ce3%2B0; sptimestamp=1774034142266; xpdpc=3DBB9E64; xpqfw=1; _pxhd=52ae14b77dcae96a68054671bc3430daaec8fea80e4212587d712d709d31628d:8dadb49b-2490-11f1-86b4-8cb0fa86473b; __pxvid=019e5637-2492-11f1-abc8-8e2c82ec81e6; _sp_ses.ad94=*; _sp_id.ad94=6861ee6a-4346-430a-8096-a458b9f3c180.1774034546.1.1774034546.1774034546.901523a5-fc60-47b7-93f2-949309e784de; akavpau_p1=1774035145~id=ef1a93ad656b914046ffedbcd03ae12a; ipSessionTrafficType=Internal; if_id=FMEZARSFC4Iuwnk2fCahZRdkZtdSjh1rquV/2Nr8MGCjY3bly13U1ZP7NlXRnHj325SZqkzgmLjqdANUf9yvlt2xGhMMmqZ95ywMTIIBHLMiZBH1G7ho/823zFcLgHiXYvCPrKdtGR8g/CTdFvFkeoyRmaxSV6z7VWTHvyBgLKgFqBpfm3OC3gh/UdR+BdRvqyYkLRI8jITf1uqwDS95flvjS9elkPBM5RKMArvnM+5tf3IWWiUx9GjESh+P/q+4h1LJrsyOWhyDWijindQq+bMYeswKd6wmh5jV3+qJT15r0QlY7Jo/Vw+ixnhffAGTXF0HwaOFvgobF/9PRA==; TS016ef4c8=010ad5cc9eae280888f0f81d43d2d3e567347c155f49bfe4b38501264c0ecda73e07b23df578224be7993fd9f83c1ffdb1d67baebd; TS01f89308=010ad5cc9eae280888f0f81d43d2d3e567347c155f49bfe4b38501264c0ecda73e07b23df578224be7993fd9f83c1ffdb1d67baebd; TS8cb5a80e027=083a77be75ab20000b7956cc924b2d38e43f929a166a2f396c25b8c6c9673af5532cdbb6f498935c085fcf506e11300058aa5e620b39c7dd632e5baa8ece97121c1e90d5c189ee36e441f6f0e205a6a5afc7158719cac3dc26a0c81805b519a7; adblocked=false; _px3=ed12c225f330dd9ac004f54921faaca45514e74923e61dbbf688d3b83fd3d57e:BzLYHQ/qnG5M8Xk4GOMP5TXH8sMD+t108lAq9W+/u6IkLps56IJCtvKD4PN58WP69mACVL+o6wsUP3XgkmlL4Q==:1000:cF/atTJzdonq2MOS8C9AI1mm8YFuJJMCWGfi62punQnsolo4y5NBFKWHSQFuCvvFYOgCveGv4Awl2DYdi1i9kOcEJ4/qXOqVAk4pba9tmwt+c03X7J1UaiiQPCYTzl/eCbmTywMhFiMJIR0v6Xo3DZnfnUrlxHFO4z1ok8X/izU8rKDhXDq/1RwMtJzJ+D/IjxWtbOF3VUQ1XjllLKwiZzNnl/Ft4ePEK2MOn2mxHHnn1y2S4bEi6dqf9bC/59KdmJQ471sOFYF32sI7E+Ey9RXa9G7wNwNMtExeOiA+iVq2ojJvcpCXGs2eL8nCVwVBCK42lcJaZfWeZGFZ53qeiI1spAighCmVb5tqkaFaCFAkBce7M98xQ+PUrTspQfDb2/eklyXjrY0laehVl+ogf2+A9wE4r8tuMCrEOQOFC/PIJ8HJQh5jk7VlZaW4fzK3IFumrGFbWdou4K9Skiu1047JhdckVxJeMNqYlBSsF7k=; akavpau_p4=1774036617~id=e8a399abf379096e009a5f792f70cbfa; _pxde=9ff38ee0a81b3f07b2a63691ca100e4341aa714fa9875480d53dd19db02498a8:eyJ0aW1lc3RhbXAiOjE3NzQwMzYxMjMwNjgsImZfa2IiOjAsImlwY19pZCI6W10sImluY19pZCI6WyJjMWY5MzZjOGQyNWE5ZDE3NTMzMGM0NWZkYjQwNjkzYSJdfQ==; akavpau_p2=1774036723~id=40dd37be5ec4c5fbfb0d50e967eba14a; _lat=MDYyMTYyMDI1HQ7TGx33ujp-SzPhFzEaLGE41o-aOlSs27QVcNgEZLgJMZB7ezTaeEk; _msit=MDYyMTYyMDI1o3ftsbBXJehcfk-cJEINgRN2YaVHmHWerRkVlxd8VnNVGw; _tsc=MTQyODYyMDIy9jJpEGI9J04OIq%2BkXt9BkbMzFujuBBzGYuoDA5St4MRnqUDYM%2Fn2yWT7xT5jQRsNg0%2BWsGpOkeM76R%2Ffk9ig6lEKiaHK43cdko2BQ9UGBkZ1OJU7aMO8WtHWSzSAGzeXIxPu4PsfSOSzQeylwKewFDP69VMXyi6jUMFlt53OcC0XiKIPJMuOoJ0jccMK%2BH1O3sYPysTeuQDsf%2BmiO2lPyMRNvKqnloGx9BrxJlDtJfEyA%2FZ1FFp%2FcvA096ga7s6m8Tfkb1r8wW%2BjHj73srg5ahoAJTZeTc58zKDcSt0X5kQWd9HENKU3NwstwWYtuXeTXKH6GQp%2FYHIzDiiCshgUSpxJzyqp0%2F7nB7equAcpI43FHNpUI1OiwYsmK6Banmn0AdHfOIU2XIacHw%3D%3D; auth=MTAyOTYyMDE4cyGsFMERyso4XPh8kh%2BAAlHosftI%2B%2ByJxBj51g7yRsUknAME0WjRmZk13QeE0aTsGBKZPyQTF%2B4eL1OWreEQYrcj2Qdw0VTxKGr0Yv%2FdDreABuZPdeU1b5hhEBS%2BJVOlfIL%2F9ZL9SEMZ34qxndB0wOXwsDjffFtqvFyhid4Kf4QDmvwWwmXjDSkF4Tyx5ySqj%2B8zOvIbbjMAhIjPSw%2FjqJTCApkOaD38FdNUdEMsTCwi05Cb%2FdVs7Fm2rlga%2Bf%2FtpvHygcd4lxYd9txaFNRynZS6EGac8RyC0x4nBEvR7qrSieDm2QAeqqlJWQ%2B246icoPfV2voSmFx22yXnTNbpyCo90PH%2FfrcB2M5ChXzP2v1wDQxhKjKHgOk2R2RWytIjYxveaNpYK3HLzMbN7gDxzSlHIeOKiSrzFFnZ42aOH1UKDkEIaQ1l3J7Xz8A99uNQv8oLwDjrFaULJKP1lhj41p6g%2BKyl9TB8kiakO7WPVdU%3D; com.wm.reflector=reflectorid:0000000000000000000000@lastupd:1774036123000@firstcreate:1774033947073; xptwg=44829560:24B8D21B4BBAA80:58E7862:BE4D7BC1:CC7D6D4A:2B6C231E:; xptwj=uz:4ebef7675a0a607423bb:EjX3sClOtMZWo9a8Za+U86Fvt0XzKT4/nhBjw05sthyH1sBcVKeQrCezbTm8DOXfRH50m9HytQPPF0ytHOCppm6p/5mGeBS+cs5tXmAlC5eLaaji/+dF5JjsgOdrdn7Nnixb/QM0ijhxPDPLhZTPP1Ed62W6dZAYeUDJNSV3AArTdnr7GaspGEGCSapdVDoaK2RVMzGTRpZKQO5FoRySpQYNsT7mH90Cv7CVjw==; TS012768cf=0184c871edd79cb20ca91690ec9d4e41edda157cfacd8bbfcbb55f5ec39c68cfbb8cef22239bda6f46b9e4632725ce2ee6e4e02b88; TS01a90220=0184c871edd79cb20ca91690ec9d4e41edda157cfacd8bbfcbb55f5ec39c68cfbb8cef22239bda6f46b9e4632725ce2ee6e4e02b88; TS2a5e0c5c027=08f4866fd3ab20000aeaba6856935979b21f4f44bd1e326da5e64eda90b4f703dd6581d6c820027608b82ae78d11300039ba2ca89110aa396299d2a44e55ec0e1e70d618a3453ddf18094e13b36ce71ad94f721947ccfd73d61b5c96dd6789bd"


def parse_cookies(raw: str) -> dict:
    cookies = {}
    for part in raw.strip().split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies


HEADERS = {
    "accept": "application/json",
    "accept-language": "en-US",
    "baggage": "trafficType=customer,deviceType=desktop,renderScope=CSR,webRequestSource=Browser,pageName=itemPage",
    "calltype": "CLIENT",
    "content-type": "application/json",
    "device_profile_ref_id": "ehwbstrvqezcjmdsog69hosdqykj1dvdzelw",
    "origin": "https://www.walmart.com",
    "referer": "https://www.walmart.com/ip/Pokemon-Trading-Card-Games-Scarlet-Violet-9-Journey-Together-Booster-Bundle/15042474261",
    "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "tenant-id": "elh9ie",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "wm_mp": "true",
    "x-apollo-operation-name": "ItemByIdBtf",
    "x-enable-server-timing": "1",
    "x-latency-trace": "1",
    "x-o-bu": "WALMART-US",
    "x-o-ccm": "server",
    "x-o-gql-query": "query ItemByIdBtf",
    "x-o-mart": "B2C",
    "x-o-platform": "rweb",
    "x-o-platform-version": "usweb-1.251.0-c88ea9c8137ed74a73df2e810aff797c113bc48a-3192043r",
    "x-o-segment": "oaoh",
}


def build_body(item_id: str) -> dict:
    return {
        "variables": {
            "isMobile": False,
            "layout": ["itemPageThreeGridDesktop2"],
            "channel": "WWW",
            "version": "v1",
            "postProcessingVersion": 1,
            "p13nCls": {
                "pageId": item_id,
                "skipPtcFetch": True,
                "p13NCallType": "BTF",
            },
            "fetchP13N": True,
            "fMrkDscrp": False,
            "pageType": "ItemPageGlobalDesktop",
            "fIdml": False,
            "fRev": False,
            "iId": item_id,
            "bbe": True,
            "fSId": True,
            "eSb": True,
            "enableDetailedBeacon": False,
            "enableMultiSave": False,
            "enableClickTrackingURL": False,
            "eCc": True,
            "fIdmlOrMrkDscrp": False,
            "tenant": "WM_GLASS",
            "epsv": True,
            "enableRxDrugScheduleModal": False,
            "enablePromotionMessages": False,
            "enableSignInToSeePrice": False,
            "enableOptimisticWeightUpdate": False,
        }
    }


def fetch_item(item_id: str, session: requests.Session) -> dict:
    url = f"https://www.walmart.com/orchestra/pdp/graphql/ItemByIdBtf/{GRAPHQL_HASH}/ip/{item_id}"
    headers = {**HEADERS, "x-o-item-id": item_id}
    body = build_body(item_id)

    print(f"[REQUEST] POST {item_id}")
    resp = session.post(url, headers=headers, json=body, timeout=10)
    print(f"[RESPONSE] {item_id} -> {resp.status_code}")

    if resp.status_code != 200:
        print(f"  Preview: {resp.text[:400]}\n")
        return {"item_id": item_id, "error": resp.status_code, "raw": resp.text[:400]}
    return {"item_id": item_id, "data": resp.json()}


def parse_item(result: dict) -> dict | None:
    if "error" in result:
        return result

    item_id = result["item_id"]
    data = result["data"]

    modules = data.get("data", {}).get("contentLayout", {}).get("modules", [])

    # The SoftBundles module contains the target item as the first product
    # with full price, availability, and seller data
    for module in modules:
        if module.get("type") != "SoftBundles":
            continue
        products = module.get("configs", {}).get("products", [])
        for product in products:
            if product.get("usItemId") != item_id:
                continue
            seller_id = product.get("sellerId", "")
            seller_name = product.get("sellerName", "")
            is_direct = (
                seller_id.upper() == WALMART_SELLER_ID
                or seller_name.lower() == "walmart.com"
            )
            price = product.get("priceInfo", {}).get("currentPrice", {}).get("price")
            # availabilityStatus + showAtc are the reliable signals here.
            # availabilityStatusV2 comes from the SoftBundles widget context and can be stale.
            availability = product.get("availabilityStatus", "UNKNOWN")
            show_atc = product.get("showAtc", False)
            return {
                "item_id": item_id,
                "name": product.get("name", "Unknown"),
                "price": price,
                "availability": availability,
                "in_stock": availability == "IN_STOCK" and show_atc,
                "walmart_direct": is_direct,
                "seller_id": seller_id,
                "seller_name": seller_name,
                "order_limit": product.get("orderLimit"),
            }

    return {"item_id": item_id, "error": "no_matching_product_in_softbundles"}


def check_items(item_ids: list[str]) -> list[dict]:
    cookies = parse_cookies(COOKIES_RAW)
    print(f"[SESSION] Loaded {len(cookies)} cookies\n")

    with requests.Session() as session:
        session.cookies.update(cookies)

        if len(item_ids) == 1:
            results = [fetch_item(item_ids[0], session)]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                futures = {pool.submit(fetch_item, iid, session): iid for iid in item_ids}
                results = [f.result() for f in concurrent.futures.as_completed(futures)]

    return results


if __name__ == "__main__":
    test_ids = [
        "15042474261",  # Pokemon Journey Together Booster Bundle
    ]

    print("=== FETCHING ===")
    raw_results = check_items(test_ids)

    print("\n=== PARSED ===")
    for r in raw_results:
        parsed = parse_item(r)
        if not parsed:
            continue
        if "error" in parsed:
            print(f"  [{parsed['item_id']}] ERROR {parsed['error']}")
        else:
            tag = "WALMART DIRECT" if parsed["walmart_direct"] else "3RD PARTY"
            stock = "IN STOCK" if parsed["in_stock"] else "OUT OF STOCK"
            print(f"  [{tag}] [{stock}] {parsed['name']}")
            print(f"    Price: ${parsed['price']} | Seller: {parsed['seller_name']} | Order limit: {parsed['order_limit']}")
