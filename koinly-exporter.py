# Copyright: Erik Lonroth <erik@dwellir.com> - Dwellir AB - 2025
# License: Apache2

import requests
import csv
import argparse
from datetime import datetime
from substrateinterface.utils.ss58 import ss58_decode
import json

def decode_compact_u128(compact_value):
    """
    Decode a compact<U128> value to a standard integer.
    
    :param compact_value: The compact<U128> value (as a string or raw value).
    :return: Decoded integer value.
    """
    if isinstance(compact_value, str):
        # Om det är en hex-sträng, konvertera till ett heltal
        if compact_value.startswith("0x"):
            return int(compact_value, 16)
        return int(compact_value)
    elif isinstance(compact_value, int):
        # Om det redan är ett heltal, returnera det
        return compact_value
    else:
        raise ValueError(f"Unsupported compact<U128> format: {compact_value}")

# Define the Koinly CSV headers
KOINLY_HEADERS = [
    "Date", "Sent Amount", "Sent Currency", "Received Amount", "Received Currency",
    "From Address", "To Address", "Fee Amount", "Fee Currency", "Label", "Tag"
]

# Function to fetch all events from Subscan API
def fetch_events(network, api_key, address):
    url = f"https://{network}.api.subscan.io/api/v2/scan/events"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key
    }
    data = {"address": address, "row": 100, "page": 0, "module": "balances"}

    events = []
    while True:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()

        if result["code"] != 0:
            raise Exception(f"Error fetching events: {result['message']}")

        for event in result["data"].get("events", []):
            if event.get("event_id") in ["Transfer", "Deposit", "Withdraw"]:
                event["block_timestamp"] = event.get("block_timestamp")
                # print(event)
                events.append(event)

        if len(result["data"].get("events", [])) < 100:
            break

        data["page"] += 1

    return events

# Function to fetch details for a specific event index
def fetch_event_details(network, api_key, event_index):
    url = f"https://{network}.api.subscan.io/api/scan/event"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key
    }
    data = {"event_index": event_index}

    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    result = response.json()

    if result["code"] != 0:
        raise Exception(f"Error fetching event details: {result['message']}")
    # print(result["data"])
    return result["data"]


def fetch_token_symbol(network, api_key, address):
    url = f"https://{network}.api.subscan.io/api/scan/account/tokens"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key
    }
    data = {"address": address}

    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    result = response.json()

    if result["code"] != 0:
        raise Exception(f"Error fetching event details: {result['message']}")
    # print(result["data"])
    return result["data"]["native"][0].get("symbol")


# Function to process events into categories
def process_events(events, network, api_key, address):
    deposit_events = []
    withdraw_events = []
    transfer_events = []

    public_address = "0x"+ss58_decode(address)

    for event in events:
        module_id = event.get("module_id")
        event_id = event.get("event_id")
        event_index = event.get("event_index")
        block_timestamp = event.get("block_timestamp")

        # Skip events with missing block_timestamp
        if block_timestamp is None:
            print(f"Skipping event with missing block_timestamp: {event}")
            continue

        # Fetch detailed event information
        if event_index:
            event_details = fetch_event_details(network, api_key, event_index)
            params = event_details.get("params", [])
        else:
            params = []

        if not params:
            print(f"Skipping event with missing params: {event}")
            continue
        currency = fetch_token_symbol(network, api_key,address)
        if module_id == "balances":
            if event_id == "Deposit":
                deposit_events.append({
                    "Date": datetime.fromtimestamp(block_timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                    "Sent Amount": "",
                    "Sent Currency": "",
                    "Received Amount": int(params[1]["value"]) / 10**12,
                    "Received Currency": currency,
                    "From Address": "",
                    "To Address": "",
                    "Fee Amount": "",
                    "Fee Currency": "",
                    "Tag": "Deposit"
                })
            elif event_id == "Withdraw":
                withdraw_events.append({
                    "Date": datetime.fromtimestamp(block_timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                    "Sent Amount": "",
                    "Sent Currency": "",
                    "Received Amount": "",
                    "Received Currency": "",
                    "From Address": "",
                    "To Address": "",
                    "Fee Amount": "-"+str(int(params[1]["value"])/10**12),
                    "Fee Currency": currency,
                    "Tag": "Withdraw"
                })
            elif event_id == "Transfer":

                # get the values using the field name if exist, otherwise get using position               
                from_address = next((p["value"] for p in params if p.get("name") == "from"), params[0]["value"])
                to_address = next((p["value"] for p in params if p.get("name") == "to"), params[1]["value"])
                amount = next((p["value"] for p in params if p.get("name") == "amount"), params[2]["value"])
                
                amount = int(amount)/10**12
                if from_address == public_address:
                    transfer_events.append({
                        "Date": datetime.fromtimestamp(block_timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                        "Sent Amount": "-"+str(amount),
                        "Sent Currency": currency,
                        "Received Amount": "",
                        "Received Currency": "",
                        "From Address": from_address,
                        "To Address": to_address,
                        "Fee Amount": "",
                        "Fee Currency": "",
                        "Tag": "Transfer"                        
                    })
                elif to_address == public_address:
                    transfer_events.append({
                        "Date": datetime.fromtimestamp(block_timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                        "Sent Amount": "",
                        "Sent Currency": "",
                        "Received Amount": amount,
                        "Received Currency": currency,
                        "From Address": from_address,
                        "To Address": to_address,
                        "Fee Amount": "",
                        "Fee Currency": "",
                        "Tag": "Received"
                    })

    return deposit_events, withdraw_events, transfer_events

# Function to write events to Koinly CSV
def write_koinly_csv(deposit_events, withdraw_events, transfer_events, output_file):
    all_events = []

    # Combine events into one list
    all_events.extend(deposit_events)
    all_events.extend(withdraw_events)
    all_events.extend(transfer_events)

    # Sort all events by date
    all_events.sort(key=lambda x: x["Date"])

    # Write to CSV
    with open(output_file, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=KOINLY_HEADERS)
        writer.writeheader()
        writer.writerows(all_events)

# Main function
def main():
    parser = argparse.ArgumentParser(description="Generate a Koinly CSV file from Subscan events.")
    parser.add_argument("network", help="The Subscan network to query (e.g., polkadot, kusama)")
    parser.add_argument("api_key", help="Your Subscan API key")
    parser.add_argument("address", help="The address to fetch events for")
    parser.add_argument("output", help="The output CSV file path")

    args = parser.parse_args()

    print("Fetching events...")
    events = fetch_events(args.network, args.api_key, args.address)

    print("Processing events...")
    deposit_events, withdraw_events, transfer_events = process_events(events, args.network, args.api_key, args.address)

    print("Writing to CSV...")
    write_koinly_csv(deposit_events, withdraw_events, transfer_events, args.output)

    print(f"Koinly CSV file generated: {args.output}")

if __name__ == "__main__":
    main()
