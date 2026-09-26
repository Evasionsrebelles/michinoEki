#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_michinoeki.py
=====================

Script SANS ARGUMENT qui scrappe l'intégralité des fiches "道の駅" (michi-no-eki)
du site officiel https://www.michi-no-eki.jp/

Fonctionnement
--------------
1. Part de https://www.michi-no-eki.jp/search et repère tous les liens de
   préfecture du type /stations/search/XX/all/all (XX = id numérique).
2. Pour chaque préfecture, parcourt toutes les pages de résultats
   (pagination ?page=N) et collecte les URLs de fiches station
   (/stations/views/NNNNN).
3. Pour chaque fiche station, extrait :
      - Préfecture
      - Titre
      - URL de la photo principale
      - Les 18 pictogrammes (Oui / Non)
      - 道の駅名, 所在地, TEL, 駐車場, 営業時間,
        ホームページ, ホームページ2, マップコード
      - Coordonnées GPS (extraites de l'iframe Google Maps intégrée)
4. Affiche en continu la progression (X/Y) et une estimation du temps
   restant (ETA) basée sur le temps moyen par fiche.
5. À la fin, exporte toutes les données dans michinoeki.json.

Dépendances (à installer avant exécution) :
    pip install requests beautifulsoup4

Usage :
    python scrape_michinoeki.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from collections import deque

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

JST = timezone(timedelta(hours=9))  # Japon n'observe pas l'heure d'été : offset fixe

BASE_URL = "https://www.michi-no-eki.jp"
SEARCH_URL = f"{BASE_URL}/search"

JSON_FILE = "michinoeki.json"              # export JSON final
UPDATE_DATE_FILE = "update_date.txt"       # fichier texte contenant la date de mise à jour

REQUEST_DELAY = 0.4          # délai (s) entre deux requêtes, pour rester poli avec le serveur
REQUEST_TIMEOUT = 20         # timeout (s) par requête
MAX_RETRIES = 3              # nombre de tentatives par requête
RETRY_BACKOFF = 2.0          # backoff exponentiel (s)
MAX_PAGES_PER_PREF = 100     # garde-fou contre boucle infinie de pagination

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 MichinoekiScraper/1.0"
    )
}

# Ordre fixe des 18 pictogrammes (tel qu'affichés sur chaque fiche station)
FACILITIES = [
    "ATM",
    "ベビーベッド",
    "レストラン",
    "軽食・喫茶",
    "宿泊施設",
    "温泉施設",
    "キャンプ場等",
    "公園",
    "展望台",
    "美術館・博物館",
    "ガソリンスタンド",
    "EV充電施設",
    "無線LAN",
    "シャワー",
    "体験施設",
    "観光案内",
    "身障者トイレ",
    "ショップ",
]

# Champs de la table d'info (dt/dd) de chaque fiche
INFO_FIELDS = [
    "道の駅名",
    "所在地",
    "TEL",
    "駐車場",
    "営業時間",
    "ホームページ",
    "ホームページ2",
    "マップコード",
]

# Association id de préfecture (tel qu'utilisé dans /stations/search/XX/all/all)
# -> Région, telle que regroupée sur la page /search du site officiel.
# NB : le site regroupe Kyushu et Okinawa dans une même rubrique
# ("九州・沖縄エリア"), mais on les sépare ici volontairement :
#   - id 56 (沖縄 / Okinawa)            -> "Okinawa"
#   - id 49 à 55 (九州 / Kyushu)         -> "Kyushu"
REGION_MAP = {
    # 北海道・東北エリア
    10: "Hokkaido-Tohoku",  # 北海道
    11: "Hokkaido-Tohoku",  # 青森
    12: "Hokkaido-Tohoku",  # 秋田
    13: "Hokkaido-Tohoku",  # 岩手
    15: "Hokkaido-Tohoku",  # 山形
    14: "Hokkaido-Tohoku",  # 宮城
    16: "Hokkaido-Tohoku",  # 福島
    # 関東エリア
    19: "Kanto",  # 群馬
    18: "Kanto",  # 栃木
    17: "Kanto",  # 茨城
    22: "Kanto",  # 東京
    21: "Kanto",  # 千葉
    23: "Kanto",  # 神奈川
    20: "Kanto",  # 埼玉
    28: "Kanto",  # 山梨
    # 北陸エリア
    24: "Hokuriku",  # 新潟
    25: "Hokuriku",  # 富山
    26: "Hokuriku",  # 石川
    # 中部エリア
    29: "Chubu",  # 長野
    31: "Chubu",  # 静岡
    32: "Chubu",  # 愛知
    30: "Chubu",  # 岐阜
    33: "Chubu",  # 三重
    # 近畿エリア
    27: "Kinki",  # 福井
    36: "Kinki",  # 大阪
    35: "Kinki",  # 京都
    37: "Kinki",  # 兵庫
    34: "Kinki",  # 滋賀
    38: "Kinki",  # 奈良
    39: "Kinki",  # 和歌山
    # 中国エリア
    43: "Chugoku",  # 広島
    42: "Chugoku",  # 岡山
    40: "Chugoku",  # 鳥取
    41: "Chugoku",  # 島根
    44: "Chugoku",  # 山口
    # 四国エリア
    46: "Shikoku",  # 香川
    47: "Shikoku",  # 愛媛
    45: "Shikoku",  # 徳島
    48: "Shikoku",  # 高知
    # 九州エリア (séparé d'Okinawa)
    49: "Kyushu",  # 福岡
    50: "Kyushu",  # 佐賀
    51: "Kyushu",  # 長崎
    52: "Kyushu",  # 熊本
    53: "Kyushu",  # 大分
    54: "Kyushu",  # 宮崎
    55: "Kyushu",  # 鹿児島
    # Okinawa (séparé de Kyushu)
    56: "Okinawa",  # 沖縄
}


def region_for_pref_id(pref_id):
    """Renvoie la Région associée à un id de préfecture, 'Inconnue' si absent du mapping."""
    return REGION_MAP.get(pref_id, "Inconnue")


# Ordre final des colonnes du json
COLUMNS = (
    ["URL", "Région", "Préfecture", "Titre", "Photo URL"]
    + FACILITIES
    + INFO_FIELDS
    + ["Coordonnées", "Mise à jour"]
)


# --------------------------------------------------------------------------- #
# Utilitaires réseau
# --------------------------------------------------------------------------- #

def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch_html(session, url):
    """Récupère le HTML d'une page, avec retries. Renvoie None en cas d'échec définitif."""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                resp.encoding = resp.apparent_encoding or "utf-8"
                return resp.text
            last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_err = str(e)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)
    print(f"  [!] Échec définitif sur {url} ({last_err})")
    return None


# --------------------------------------------------------------------------- #
# Phase 1 : découverte des préfectures
# --------------------------------------------------------------------------- #

def discover_prefectures(session):
    """Repère tous les liens /stations/search/XX/all/all depuis la page /search."""
    html = fetch_html(session, SEARCH_URL)
    if html is None:
        print("Impossible de récupérer la page /search. Arrêt.")
        sys.exit(1)

    ids = sorted(set(int(m) for m in re.findall(
        r"/stations/search/(\d+)/all/all", html
    )))
    prefs = [(pid, f"{BASE_URL}/stations/search/{pid}/all/all") for pid in ids]
    print(f"[Découverte] {len(prefs)} préfectures trouvées sur /search.")
    return prefs


# --------------------------------------------------------------------------- #
# Phase 2 : découverte des URLs de stations (avec pagination)
# --------------------------------------------------------------------------- #

def discover_station_urls_for_pref(session, pref_id, pref_url):
    found_all = set()
    page = 0
    while page < MAX_PAGES_PER_PREF:
        page_url = pref_url if page == 0 else f"{pref_url}?page={page}"
        html = fetch_html(session, page_url)
        time.sleep(REQUEST_DELAY)
        if html is None:
            break

        ids_on_page = set(re.findall(r"/stations/views/(\d+)", html))
        if not ids_on_page or ids_on_page.issubset(found_all):
            # page vide ou déjà vue -> fin de la pagination pour cette préfecture
            break

        found_all |= ids_on_page
        page += 1

    urls = {f"{BASE_URL}/stations/views/{sid}" for sid in found_all}
    return urls


def discover_all_station_urls(session, prefectures):
    """Renvoie (liste triée des URLs, dict URL -> pref_id)."""
    all_urls = set()
    url_to_pref_id = {}
    for i, (pref_id, pref_url) in enumerate(prefectures, start=1):
        urls = discover_station_urls_for_pref(session, pref_id, pref_url)
        all_urls |= urls
        for u in urls:
            # Si une même fiche apparaissait (anormalement) sous plusieurs
            # préfectures, on garde la dernière rencontrée sans planter.
            url_to_pref_id[u] = pref_id
        print(f"[Découverte] Préfecture {pref_id} ({i}/{len(prefectures)}) "
              f"-> {len(urls)} stations (total cumulé : {len(all_urls)})")
    return sorted(all_urls), url_to_pref_id


# --------------------------------------------------------------------------- #
# Phase 3 : parsing d'une fiche station
# --------------------------------------------------------------------------- #

def parse_station_page(html, url, region=""):
    soup = BeautifulSoup(html, "html.parser")
    data = {col: "" for col in COLUMNS}
    data["URL"] = url
    data["Région"] = region

    # Préfecture + Titre
    title_block = soup.select_one("div.viewTitle")
    if title_block:
        span = title_block.find("span")
        if span:
            data["Préfecture"] = span.get_text(strip=True)
        h2 = title_block.find("h2")
        if h2:
            data["Titre"] = h2.get_text(strip=True)

    # Photo (première image de la galerie principale)
    img = soup.select_one("div.viewGallery__main .swiper-slide img")
    if img and img.get("src"):
        data["Photo URL"] = img["src"]

    # Pictogrammes (18)
    facility_map = {}
    fac_ul = soup.select_one("div.viewFacility ul")
    if fac_ul:
        for li in fac_ul.find_all("li"):
            span = li.find("span")
            if not span:
                continue
            label = span.get_text(strip=True)
            classes = li.get("class") or []
            facility_map[label] = "Non" if "off" in classes else "Oui"
    for label in FACILITIES:
        data[label] = facility_map.get(label, "N/A")

    # Infos (道の駅名, 所在地, TEL, 駐車場, 営業時間, ホームページ, ホームページ2, マップコード)
    info_map = {}
    for dl in soup.select("div.info dl"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if dt and dd:
            key = dt.get_text(strip=True)
            value = dd.get_text(strip=True)
            info_map[key] = value
    for field in INFO_FIELDS:
        data[field] = info_map.get(field, "")

    # Coordonnées (iframe Google Maps : .../embed/v1/place?q=LAT,LNG&key=...)
    iframe = soup.select_one("div.viewMap iframe")
    if iframe and iframe.get("src"):
        m = re.search(r"q=(-?\d+\.\d+),(-?\d+\.\d+)", iframe["src"])
        if m:
            data["Coordonnées"] = f"{m.group(1)},{m.group(2)}"

    return data


# --------------------------------------------------------------------------- #
# Export JSON
# --------------------------------------------------------------------------- #

def export_update_date_file(update_date, json_path):
    """Écrit la date de mise à jour dans update_date.txt, dans le même
    dossier que le fichier JSON exporté."""
    folder = os.path.dirname(os.path.abspath(json_path))
    txt_path = os.path.join(folder, UPDATE_DATE_FILE)
    tmp_path = txt_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(update_date + "\n")
    os.replace(tmp_path, txt_path)  # écriture atomique
    return txt_path


def export_json(scraped_dict, path):
    # Date et heure de mise à jour du fichier (instant de l'export, converti en
    # heure du Japon), au format DD/MM/YYYY HH:MM, appliquée à toutes les
    # fiches ET écrite à part dans update_date.txt.
    update_date = datetime.now(JST).strftime("%d/%m/%Y %H:%M")
    for record in scraped_dict.values():
        record["Mise à jour"] = update_date

    records = [scraped_dict[url] for url in sorted(scraped_dict.keys())]
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)  # écriture atomique

    export_update_date_file(update_date, path)


# --------------------------------------------------------------------------- #
# ETA / affichage progression
# --------------------------------------------------------------------------- #

def format_duration(seconds):
    if seconds < 0 or seconds != seconds:  # NaN guard
        return "?"
    return str(timedelta(seconds=int(seconds)))


class ProgressTracker:
    """Calcule un temps moyen glissant par URL traitée pour estimer l'ETA."""

    def __init__(self, window=25):
        self.durations = deque(maxlen=window)

    def add(self, duration):
        self.durations.append(duration)

    def average(self):
        if not self.durations:
            return None
        return sum(self.durations) / len(self.durations)

    def eta(self, remaining):
        avg = self.average()
        if avg is None:
            return "calcul en cours..."
        return format_duration(avg * remaining)


# --------------------------------------------------------------------------- #
# Programme principal
# --------------------------------------------------------------------------- #

def main():
    session = make_session()

    # --- Phase 1 & 2 : découverte ---
    prefectures = discover_prefectures(session)
    all_urls, url_to_pref_id = discover_all_station_urls(session, prefectures)
    print(f"[Découverte] Terminée : {len(all_urls)} fiches station au total.\n")

    total = len(all_urls)
    scraped = {}  # dict url -> data
    failed = 0

    tracker = ProgressTracker()
    done_count = 0

    # --- Phase 3 : scraping des fiches ---
    for url in all_urls:
        t0 = time.time()
        html = fetch_html(session, url)
        time.sleep(REQUEST_DELAY)

        if html is None:
            print(f"  [!] Ignoré (échec réseau) : {url}")
            failed += 1
            continue

        try:
            pref_id = url_to_pref_id.get(url)
            region = region_for_pref_id(pref_id) if pref_id is not None else ""
            data = parse_station_page(html, url, region=region)
        except Exception as e:
            print(f"  [!] Erreur de parsing sur {url} : {e}")
            failed += 1
            continue

        scraped[url] = data
        done_count += 1

        duration = time.time() - t0
        tracker.add(duration)
        remaining = total - done_count - failed
        eta = tracker.eta(remaining)

        print(f"[{done_count}/{total}] {data.get('Titre', '?')} "
              f"({data.get('Préfecture', '?')}) — ETA restant : {eta}")

    # Export final
    export_json(scraped, JSON_FILE)

    print("\n=== Terminé ===")
    print(f"Fiches scrappées : {len(scraped)}/{total}")
    print(f"JSON : {os.path.abspath(JSON_FILE)}")
    print(f"Date de mise à jour : {os.path.abspath(UPDATE_DATE_FILE)}")
    if failed:
        print(f"{failed} fiche(s) n'ont pas pu être récupérées "
              "(erreurs réseau ou parsing). Relancez le script pour réessayer.")


if __name__ == "__main__":
    main()
