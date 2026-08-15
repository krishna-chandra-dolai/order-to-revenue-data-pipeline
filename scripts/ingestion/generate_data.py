"""Generate deterministic synthetic e-commerce source data.

The records are fictional. A small, controlled anomaly manifest makes every
intentional quality or reconciliation case easy to trace and explain.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


SEED = 20260814
CUSTOMER_COUNT = 2_000
PRODUCT_COUNT = 300
ORDER_COUNT = 10_000

FIRST_NAMES = [
    "Aarav", "Aditi", "Aditya", "Akash", "Ananya", "Aniket", "Anjali", "Arjun",
    "Asha", "Bhavna", "Deepak", "Dev", "Diya", "Farhan", "Gaurav", "Harini",
    "Ishaan", "Ishita", "Kabir", "Karthik", "Kavya", "Meera", "Mohit", "Naina",
    "Neha", "Nikhil", "Pooja", "Pranav", "Priya", "Rahul", "Riya", "Rohan",
    "Saanvi", "Sahil", "Sanjay", "Shreya", "Sneha", "Tanvi", "Varun", "Vikram",
]

LAST_NAMES = [
    "Agarwal", "Banerjee", "Bhat", "Chauhan", "Das", "Desai", "Gupta", "Iyer",
    "Jain", "Joshi", "Kapoor", "Khan", "Kulkarni", "Kumar", "Malhotra", "Mehta",
    "Menon", "Mishra", "Nair", "Patel", "Rao", "Reddy", "Saxena", "Shah",
    "Sharma", "Singh", "Sinha", "Trivedi", "Verma", "Yadav",
]

LOCATIONS = [
    ("Ahmedabad", "Gujarat"), ("Bengaluru", "Karnataka"), ("Bhopal", "Madhya Pradesh"),
    ("Bhubaneswar", "Odisha"), ("Chandigarh", "Chandigarh"), ("Chennai", "Tamil Nadu"),
    ("Coimbatore", "Tamil Nadu"), ("Delhi", "Delhi"), ("Gurugram", "Haryana"),
    ("Guwahati", "Assam"), ("Hyderabad", "Telangana"), ("Indore", "Madhya Pradesh"),
    ("Jaipur", "Rajasthan"), ("Kochi", "Kerala"), ("Kolkata", "West Bengal"),
    ("Lucknow", "Uttar Pradesh"), ("Mumbai", "Maharashtra"), ("Mysuru", "Karnataka"),
    ("Nagpur", "Maharashtra"), ("Noida", "Uttar Pradesh"), ("Patna", "Bihar"),
    ("Pune", "Maharashtra"), ("Surat", "Gujarat"), ("Thiruvananthapuram", "Kerala"),
    ("Visakhapatnam", "Andhra Pradesh"),
]

# Each category contains real-looking brand/family pairs and clear variants.
# Combining these fixed values gives 300 meaningful, unique catalog entries.
CATALOG = {
    "Laptops": {
        "models": [("Dell", "Inspiron"), ("HP", "Pavilion"), ("Lenovo", "IdeaPad"),
                   ("Asus", "Vivobook"), ("Acer", "Aspire"), ("Apple", "MacBook Air"),
                   ("MSI", "Modern"), ("Samsung", "Galaxy Book"),
                   ("Dell", "Vostro"), ("Lenovo", "ThinkBook")],
        "variants": [("14-inch 8GB/512GB", 48_990), ("15-inch 16GB/512GB", 62_990),
                     ("14-inch 16GB/1TB", 74_990), ("15-inch 16GB/1TB", 86_990)],
    },
    "Smartphones": {
        "models": [("Samsung", "Galaxy A"), ("Apple", "iPhone"), ("OnePlus", "Nord"),
                   ("Xiaomi", "Redmi Note"), ("Motorola", "Moto G"), ("Google", "Pixel"),
                   ("Nothing", "Phone"), ("Realme", "Narzo"), ("Vivo", "V Series"),
                   ("Oppo", "Reno")],
        "variants": [("6GB/128GB", 16_999), ("8GB/128GB", 22_999),
                     ("8GB/256GB", 31_999), ("12GB/256GB", 44_999)],
    },
    "Monitors": {
        "models": [("Dell", "UltraSharp"), ("LG", "UltraGear"), ("Samsung", "Odyssey"),
                   ("BenQ", "GW Series"), ("Acer", "Nitro"), ("Asus", "ProArt"),
                   ("Lenovo", "ThinkVision"), ("HP", "M Series"), ("MSI", "Optix"),
                   ("ViewSonic", "ColorPro")],
        "variants": [("22-inch FHD", 9_499), ("24-inch FHD", 13_499),
                     ("27-inch QHD", 24_999), ("32-inch 4K", 39_999)],
    },
    "Keyboards": {
        "models": [("Logitech", "K Series"), ("Keychron", "K Pro"), ("Dell", "KB Series"),
                   ("HP", "350 Series"), ("Redragon", "Kumara"), ("Razer", "BlackWidow"),
                   ("Corsair", "K Series"), ("Zebronics", "Companion"),
                   ("Portronics", "Hydra"), ("Microsoft", "Designer")],
        "variants": [("Wired Full Size", 1_299), ("Wireless Compact", 2_499),
                     ("Mechanical Tactile", 5_499), ("Mechanical Wireless", 8_499)],
    },
    "Mice": {
        "models": [("Logitech", "M Series"), ("Dell", "MS Series"), ("HP", "Z Series"),
                   ("Razer", "DeathAdder"), ("Corsair", "Harpoon"), ("Lenovo", "Go"),
                   ("Zebronics", "Transformer"), ("Portronics", "Toad"),
                   ("Microsoft", "Modern Mobile"), ("Asus", "TUF Gaming")],
        "variants": [("Wired 1200 DPI", 599), ("Wireless 1600 DPI", 1_199),
                     ("Bluetooth Silent", 1_899), ("Gaming 8000 DPI", 3_499)],
    },
    "Headphones": {
        "models": [("Sony", "WH Series"), ("JBL", "Tune"), ("Boat", "Rockerz"),
                   ("Sennheiser", "HD Series"), ("Audio-Technica", "ATH Series"),
                   ("Samsung", "Galaxy Buds"), ("OnePlus", "Buds"), ("Realme", "Buds Air"),
                   ("Noise", "Airwave"), ("HyperX", "Cloud")],
        "variants": [("Wired Stereo", 1_499), ("Wireless On-Ear", 2_999),
                     ("TWS Noise Cancelling", 5_999), ("Over-Ear ANC", 9_999)],
    },
    "Storage Devices": {
        "models": [("Samsung", "T7 SSD"), ("Western Digital", "My Passport"),
                   ("Seagate", "Expansion"), ("SanDisk", "Extreme SSD"),
                   ("Crucial", "X Series SSD"), ("Kingston", "XS SSD"),
                   ("Transcend", "StoreJet"), ("Toshiba", "Canvio"),
                   ("Lexar", "JumpDrive"), ("HP", "Portable SSD")],
        "variants": [("500GB", 4_299), ("1TB", 7_499), ("2TB", 13_999)],
    },
    "Accessories": {
        "models": [("Anker", "USB-C Hub"), ("Belkin", "GaN Charger"),
                   ("Logitech", "Webcam"), ("TP-Link", "Wi-Fi Adapter"),
                   ("Amazon Basics", "Laptop Stand"), ("Portronics", "USB-C Dock"),
                   ("Targus", "Laptop Sleeve"), ("Dell", "Power Adapter"),
                   ("Spigen", "Phone Stand"), ("CableCreation", "Display Cable")],
        "variants": [("Standard", 999), ("Plus", 1_999), ("Premium", 3_499)],
    },
}

EXPECTED_ANOMALIES = {
    "invalid_quantity": 12,
    "missing_customer_reference": 10,
    "missing_product_reference": 10,
    "payment_amount_mismatch": 15,
    "failed_payment": 25,
    "late_arriving_payment": 10,
    "missing_payment": 10,
}


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def money(value: Decimal | int | float) -> str:
    return f"{Decimal(value).quantize(Decimal('0.01')):.2f}"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", value.lower()).strip(".")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def generate(output_dir: Path) -> dict:
    rng = random.Random(SEED)
    output_dir.mkdir(parents=True, exist_ok=True)

    customer_start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    customers = []
    for customer_id in range(1, CUSTOMER_COUNT + 1):
        first = FIRST_NAMES[(customer_id - 1) % len(FIRST_NAMES)]
        last = LAST_NAMES[((customer_id - 1) // len(FIRST_NAMES)) % len(LAST_NAMES)]
        city, state = LOCATIONS[rng.randrange(len(LOCATIONS))]
        created_at = customer_start + timedelta(days=rng.randrange(365), seconds=rng.randrange(86_400))
        updated_at = created_at + timedelta(days=rng.randrange(0, 91))
        customers.append({
            "customer_id": customer_id,
            "customer_name": f"{first} {last}",
            "email": f"{slug(first)}.{slug(last)}.{customer_id}@example.test",
            "city": city,
            "state": state,
            "created_at": iso(created_at),
            "updated_at": iso(updated_at),
        })

    products = []
    product_id = 1
    product_start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    for category, definition in CATALOG.items():
        for brand, family in definition["models"]:
            for variant, base_price in definition["variants"]:
                price_adjustment = Decimal((product_id % 5) * 100)
                created_at = product_start + timedelta(days=product_id % 120)
                products.append({
                    "product_id": product_id,
                    "product_name": f"{brand} {family} - {variant}",
                    "category": category,
                    "brand": brand,
                    "unit_price": money(Decimal(base_price) + price_adjustment),
                    "created_at": iso(created_at),
                    "updated_at": iso(created_at + timedelta(days=product_id % 30)),
                })
                product_id += 1

    if len(products) != PRODUCT_COUNT:
        raise RuntimeError(f"Catalog produced {len(products)} products, expected {PRODUCT_COUNT}")

    selected_ids = rng.sample(range(1, ORDER_COUNT + 1), sum(EXPECTED_ANOMALIES.values()))
    anomaly_ids: dict[str, set[int]] = {}
    cursor = 0
    for anomaly_type, count in EXPECTED_ANOMALIES.items():
        anomaly_ids[anomaly_type] = set(selected_ids[cursor:cursor + count])
        cursor += count

    anomaly_manifest = []
    descriptions = {
        "invalid_quantity": "Order quantity is zero or negative and must be rejected.",
        "missing_customer_reference": "Order customer_id is null and cannot be matched.",
        "missing_product_reference": "Order product_id is null and cannot be matched.",
        "payment_amount_mismatch": "Successful payment differs from quantity multiplied by unit price.",
        "failed_payment": "Failed payment is valid business data but is not confirmed revenue.",
        "late_arriving_payment": "Successful payment arrives more than seven days after the order.",
        "missing_payment": "Order has no payment record in the initial extract.",
    }
    for anomaly_type, ids in anomaly_ids.items():
        for order_id in sorted(ids):
            anomaly_manifest.append({
                "scenario_type": anomaly_type,
                "order_id": order_id,
                "description": descriptions[anomaly_type],
            })

    order_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    order_span_seconds = int((datetime(2025, 7, 1, tzinfo=timezone.utc) - order_start).total_seconds())
    forced_completed = set().union(*anomaly_ids.values())
    orders = []
    payments = []
    payment_id = 1
    order_status_counts: Counter[str] = Counter()
    payment_status_counts: Counter[str] = Counter()

    for order_id in range(1, ORDER_COUNT + 1):
        chosen_customer_id = rng.randint(1, CUSTOMER_COUNT)
        chosen_product_id = rng.randint(1, PRODUCT_COUNT)
        product_price = Decimal(products[chosen_product_id - 1]["unit_price"])
        quantity = rng.choices([1, 2, 3, 4, 5], weights=[54, 25, 12, 6, 3], k=1)[0]
        if order_id in anomaly_ids["invalid_quantity"]:
            quantity = 0 if order_id % 2 == 0 else -1

        customer_id_value: int | str = chosen_customer_id
        product_id_value: int | str = chosen_product_id
        if order_id in anomaly_ids["missing_customer_reference"]:
            customer_id_value = ""
        if order_id in anomaly_ids["missing_product_reference"]:
            product_id_value = ""

        if order_id in forced_completed:
            order_status = "completed"
        else:
            order_status = rng.choices(
                ["completed", "pending", "cancelled", "returned"],
                weights=[90, 3, 4, 3],
                k=1,
            )[0]

        created_at = order_start + timedelta(seconds=rng.randrange(order_span_seconds))
        updated_at = created_at + timedelta(hours=rng.randrange(0, 73))
        orders.append({
            "order_id": order_id,
            "customer_id": customer_id_value,
            "product_id": product_id_value,
            "quantity": quantity,
            "unit_price": money(product_price),
            "order_status": order_status,
            "created_at": iso(created_at),
            "updated_at": iso(updated_at),
        })
        order_status_counts[order_status] += 1

        if order_id in anomaly_ids["missing_payment"]:
            continue

        if order_id in anomaly_ids["failed_payment"]:
            payment_status = "failed"
        elif order_status in {"cancelled", "returned"}:
            payment_status = "refunded"
        elif order_status == "pending":
            payment_status = "pending"
        else:
            payment_status = "successful"

        expected_amount = product_price * Decimal(max(quantity, 1))
        payment_amount = expected_amount
        if order_id in anomaly_ids["payment_amount_mismatch"]:
            difference = Decimal(50 + (order_id % 8) * 25)
            payment_amount = expected_amount + difference

        if order_id in anomaly_ids["late_arriving_payment"]:
            payment_time = created_at + timedelta(days=8 + order_id % 5, hours=2)
        else:
            payment_time = created_at + timedelta(hours=rng.randrange(0, 49))

        payment_updated_at = payment_time + timedelta(hours=rng.randrange(0, 25))
        payments.append({
            "payment_id": payment_id,
            "order_id": order_id,
            "payment_amount": money(payment_amount),
            "payment_method": rng.choices(
                ["UPI", "Card", "Net Banking", "COD"],
                weights=[43, 31, 15, 11],
                k=1,
            )[0],
            "payment_status": payment_status,
            "payment_time": iso(payment_time),
            "updated_at": iso(payment_updated_at),
        })
        payment_status_counts[payment_status] += 1
        payment_id += 1

    write_csv(output_dir / "customers.csv", list(customers[0]), customers)
    write_csv(output_dir / "products.csv", list(products[0]), products)
    write_csv(output_dir / "orders.csv", list(orders[0]), orders)
    write_csv(output_dir / "payments.csv", list(payments[0]), payments)
    write_csv(
        output_dir / "anomaly_manifest.csv",
        ["scenario_type", "order_id", "description"],
        sorted(anomaly_manifest, key=lambda row: (row["scenario_type"], row["order_id"])),
    )

    summary = {
        "synthetic_data": True,
        "seed": SEED,
        "row_counts": {
            "customers": len(customers),
            "products": len(products),
            "orders": len(orders),
            "payments": len(payments),
        },
        "anomaly_counts": EXPECTED_ANOMALIES,
        "total_controlled_scenarios": sum(EXPECTED_ANOMALIES.values()),
        "order_status_counts": dict(sorted(order_status_counts.items())),
        "payment_status_counts": dict(sorted(payment_status_counts.items())),
    }
    (output_dir / "generation_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    default_output = Path(__file__).resolve().parents[2] / "data" / "generated"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=default_output)
    args = parser.parse_args()
    summary = generate(args.output_dir.resolve())
    print(json.dumps(summary, indent=2))
    print(f"Generated files in {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
