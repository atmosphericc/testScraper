import requests
import csv

def get_target_product_info(tcin):
    url = "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v1"
    params = {
        "key": "eb2551e4accc14c4a1eedadacf36561c",  # Public API key
        "tcin": tcin
    }

    try:
        response = requests.get(url, params=params)
        data = response.json()

        product = data['data']['product']
        item = product['item']
        title = item['product_description']['title']
        price = product['price']['formatted_current_price']
        buy_url = item['enrichment']['buy_url']
        in_stock = product['fulfillment']['purchase_limit'] > 0

        return {
            'tcin': tcin,
            'title': title,
            'price': price,
            'in_stock': in_stock,
            'url': buy_url
        }

    except Exception as e:
        return {
            'tcin': tcin,
            'title': 'Error fetching data',
            'price': 'N/A',
            'in_stock': False,
            'url': '#'
        }

def load_tcin_list(csv_path='tcins.csv'):
    with open(csv_path, newline='') as f:
        reader = csv.reader(f)
        return [row[0] for row in reader if row]
