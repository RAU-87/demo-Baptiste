"""Local gift watchlist. Requires Python 3.10+ and no third-party packages."""
import argparse
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("CADEAUX_DATA", ROOT / "data" / "cadeaux.json"))
PARIS = ZoneInfo("Europe/Paris")
HOURS = (8, 14, 20)
MERCHANTS = {
    "www.fnac.com": ("Fnac", r"/[^?#\s]+/a\d+/w-4/?"),
    "www.lego.com": ("LEGO", r"/fr-fr/product/[\w-]+-\d+/?"),
}
STATIC = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
    "/pere-noel.webp": ("pere-noel.webp", "image/webp"),
}


def clean_link(value):
    url = urlsplit(value.strip())
    merchant = MERCHANTS.get(url.netloc.lower())
    if url.scheme != "https" or not merchant or not re.fullmatch(merchant[1], url.path):
        raise ValueError("Colle une URL de fiche produit Fnac ou LEGO France.")
    return f"https://{url.netloc.lower()}{url.path.rstrip('/')}"


class ProductRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            link = clean_link(newurl)
            if urlsplit(link).hostname != urlsplit(req.full_url).hostname:
                raise ValueError()
        except ValueError:
            raise HTTPError(req.full_url, code, "Redirection hors fiche produit", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, link)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def cents(value):
    try:
        amount = Decimal(re.sub(r"\s+", "", str(value)).replace(",", "."))
        if amount.is_finite() and 0 <= amount <= 100_000 and amount == amount.quantize(Decimal(".01")):
            return int(amount * 100)
    except InvalidOperation:
        pass
    raise ValueError("Montant en euros illisible ou invalide.")


def products(item):
    if isinstance(item, list):
        for child in item:
            yield from products(child)
    elif isinstance(item, dict):
        if item.get("@type") == "Product" or "Product" in (item.get("@type") or []):
            yield item
        yield from products(item.get("@graph"))


def extract_offer(html, merchant):
    scripts = re.findall(r'<script\b[^>]*type=["\x27]application/ld\+json["\x27][^>]*>(.*?)</script\s*>', html, re.I | re.S)
    for raw in scripts:
        try:
            structured = json.loads(raw)
        except ValueError:
            continue
        for product in products(structured):
            offers = product.get("offers")
            for offer in offers if isinstance(offers, list) else [offers]:
                if not isinstance(offer, dict) or offer.get("priceCurrency") != "EUR" or "price" not in offer:
                    continue
                if offer.get("itemCondition", "").split("/")[-1] in {"UsedCondition", "RefurbishedCondition", "DamagedCondition"}:
                    continue
                price = cents(offer["price"])
                seller = offer.get("seller")
                seller_name = seller.get("name") if isinstance(seller, dict) else None
                shipping = None
                delivery = None
                if merchant == "LEGO":
                    shipping = 595 if price < 5500 else 0
                elif len(offers if isinstance(offers, list) else [offers]) == 1:
                    block = re.search(r'<p class="f-buyBox-shipping">(.*?)</p>', html, re.S)
                    if block:
                        fee = re.search(r'class="f-deliveryInfo__price"[^>]*>\s*([\d.,\s]+)€', block[1])
                        free = re.search(r'class="f-deliveryInfo__price"[^>]*>\s*(Offert[e]?|Gratuit[e]?)\s*<', block[1], re.I)
                        date = re.search(r'class="f-buyBox-deliveryTime"[^>]*>(.*?)</span>', block[1], re.S)
                        shipping = cents(fee[1]) if fee else (0 if free else None)
                        delivery = " ".join(unescape(re.sub(r"<[^>]*>", "", date[1])).split()) if date else None
                    elif seller_name == "FNAC.COM":
                        standard = re.search(
                            r'<div class="f-buyBox-shipping">\s*'
                            r'<div class="f-deliveryInfo"[^>]*>\s*'
                            r'<span class="f-deliveryInfo__title">\s*Standard\s*</span>\s*'
                            r'<span class="f-deliveryInfo__price">([^<]+)</span>\s*</div>'
                            r'(?:\s*<span class="f-deliveryInfo__date">([^<]+)</span>)?',
                            html, re.I,
                        )
                        if standard:
                            label = unescape(standard[1]).strip()
                            if label.casefold() in {"gratuit", "gratuite", "offert", "offerte"}:
                                shipping = 0
                            elif re.fullmatch(r"[\d.,\s]+€", label):
                                shipping = cents(label.removesuffix("€"))
                            if standard[2]:
                                delivery = " ".join(unescape(standard[2]).split())
                return {
                    "product": product.get("name"), "seller": seller_name or merchant,
                    "price": price, "shipping": shipping,
                    "total": price + shipping if shipping is not None else None,
                    "stock": offer.get("availability", "").split("/")[-1],
                    "delivery": delivery, "checkedAt": timestamp(),
                }
    raise ValueError("Prix en euros indisponible sur la fiche.")


def read_offer(link):
    request = Request(clean_link(link), headers={
        "User-Agent": "DemoBaptiste/1.0 (suivi prive de prix)",
        "Accept": "text/html", "Accept-Language": "fr-FR,fr;q=0.9",
    })
    with build_opener(ProductRedirect()).open(request, timeout=9) as response:
        html = response.read(4_000_000).decode("utf-8", errors="replace")
    return extract_offer(html, MERCHANTS[urlsplit(link).hostname][0])


class Watchlist:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = json.loads(DATA.read_text()) if DATA.exists() else {"gifts": [], "lastRun": None, "slot": None}
        self.running = False

    def save(self):
        DATA.parent.mkdir(parents=True, exist_ok=True)
        temporary = DATA.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(DATA)

    def view(self):
        now = datetime.now(PARIS)
        future = [now.replace(hour=hour, minute=0, second=0, microsecond=0) for hour in HOURS]
        next_run = next((slot for slot in future if slot > now), None)
        if next_run is None:
            next_run = future[0] + timedelta(days=1)
        with self.lock:
            return {**self.state, "running": self.running, "nextRun": next_run.isoformat(),
                    "gifts": json.loads(json.dumps(self.state["gifts"]))}

    def put(self, data, gift_id=None):
        name = data["name"].strip()
        if not 1 <= len(name) <= 100:
            raise ValueError("Indique un nom de cadeau (100 caractères maximum).")
        links = list(dict.fromkeys(clean_link(link) for link in data["links"] if link.strip()))
        if not 1 <= len(links) <= 6:
            raise ValueError("Ajoute entre une et six fiches produit.")
        target = cents(data["target"]) if data.get("target") not in ("", None) else None
        with self.lock:
            old = next((gift for gift in self.state["gifts"] if gift["id"] == gift_id), None)
            if gift_id and old is None:
                raise KeyError()
            current = {source["link"]: source for source in old["sources"]} if old else {}
            gift = {"id": gift_id or uuid4().hex, "name": name, "target": target,
                    "sources": [current.get(link, {"link": link, "shop": MERCHANTS[urlsplit(link).hostname][0],
                                 "offer": None, "error": None}) for link in links]}
            if old:
                self.state["gifts"][self.state["gifts"].index(old)] = gift
            else:
                self.state["gifts"].append(gift)
            self.save()
        return gift["id"]

    def delete(self, gift_id):
        with self.lock:
            gifts = self.state["gifts"]
            gift = next((item for item in gifts if item["id"] == gift_id), None)
            if gift is None:
                raise KeyError()
            gifts.remove(gift)
            self.save()

    def start(self, gift_id=None, slot=None):
        with self.lock:
            if self.running or (slot and self.state["slot"] == slot):
                return False
            jobs = {(gift["id"], source["link"]) for gift in self.state["gifts"]
                    if gift_id is None or gift["id"] == gift_id for source in gift["sources"]}
            if not jobs:
                return False
            self.running = True
        threading.Thread(target=self.collect, args=(jobs, slot), daemon=True).start()
        return True

    def collect(self, jobs, slot):
        results = {}
        try:
            for link in sorted({link for _, link in jobs}):
                try:
                    results[link] = (read_offer(link), None)
                except HTTPError as error:
                    results[link] = (None, f"Fiche indisponible (HTTP {error.code}).")
                except (URLError, OSError):
                    results[link] = (None, "Connexion à la boutique impossible.")
                except (ValueError, TypeError, AttributeError):
                    results[link] = (None, "Prix en euros indisponible sur la fiche.")
            with self.lock:
                for gift in self.state["gifts"]:
                    for source in gift["sources"]:
                        link = source["link"]
                        if (gift["id"], link) not in jobs:
                            continue
                        offer, source["error"] = results[link]
                        if offer:
                            source["offer"] = offer
                self.state["lastRun"] = timestamp()
                if slot:
                    self.state["slot"] = slot
                self.save()
        finally:
            with self.lock:
                self.running = False

    def schedule(self):
        stop = threading.Event()
        while True:
            now = datetime.now(PARIS)
            hour = max((hour for hour in HOURS if hour <= now.hour), default=None)
            if hour is not None:
                self.start(slot=f"{now.date()}T{hour:02}")
            stop.wait(15)


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, data, kind="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", kind if kind.startswith("image/") else f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def json(self, code, payload):
        self.reply(code, json.dumps(payload, ensure_ascii=False).encode())

    def do_GET(self):
        if self.path == "/api/gifts":
            self.json(200, watchlist.view())
        elif self.path in STATIC:
            file, kind = STATIC[self.path]
            self.reply(200, (ROOT / "web" / file).read_bytes(), kind)
        else:
            self.json(404, {"error": "Page inconnue."})

    def mutate(self):
        if self.headers.get("X-Cadeaux-App") != "local":
            self.json(403, {"error": "Requête refusée."})
            return
        match = re.fullmatch(r"/api/gifts/([0-9a-f]{32})", self.path)
        try:
            if self.command == "POST" and self.path == "/api/collect":
                self.json(200, {"started": watchlist.start()})
            elif self.command == "DELETE" and match:
                watchlist.delete(match[1])
                self.json(200, {"ok": True})
            elif (self.command == "POST" and self.path == "/api/gifts") or (self.command == "PUT" and match):
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16000:
                    raise ValueError("Formulaire vide ou trop volumineux.")
                payload = json.loads(self.rfile.read(size))
                gift_id = watchlist.put(payload, match[1] if match else None)
                self.json(200 if match else 201, {"id": gift_id, "started": watchlist.start(gift_id)})
            else:
                self.json(404, {"error": "Page inconnue."})
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            self.json(400 if not isinstance(error, KeyError) else 404,
                      {"error": str(error) if isinstance(error, ValueError) else "Cadeau ou formulaire introuvable."})

    do_POST = do_PUT = do_DELETE = mutate


if __name__ == "__main__":
    watchlist = Watchlist()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "3000")))
    parser.add_argument("--host", choices=["127.0.0.1", "localhost"], default="127.0.0.1")
    parser.add_argument("--strictPort", action="store_true", help="fail if the requested port is already in use")
    args = parser.parse_args()
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        threading.Thread(target=watchlist.schedule, daemon=True).start()
        print(f"Local: http://localhost:{server.server_port}", flush=True)
        server.serve_forever()
