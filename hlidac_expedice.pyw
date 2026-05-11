import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import webbrowser
import base64
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from pathlib import Path
import threading

env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# ==========================================
# KONFIGURACE A ÚDAJE
# ==========================================
CLIENT_ID             = os.getenv("ALLEGRO_CLIENT_ID")
CLIENT_SECRET         = os.getenv("ALLEGRO_CLIENT_SECRET")
INITIAL_ACCESS_TOKEN  = os.getenv("ALLEGRO_ACCESS_TOKEN")
INITIAL_REFRESH_TOKEN = os.getenv("ALLEGRO_REFRESH_TOKEN")

SELLER_ID = os.getenv("ALLEGRO_SELLER_ID")

TOPTRANS_USER = os.getenv("TOPTRANS_USER")
TOPTRANS_PASS = os.getenv("TOPTRANS_PASS")
TOPTRANS_API  = "https://zp.toptrans.cz/api/json"

LOG_DIR = f"{os.getenv('LOCALAPPDATA')}\\logy_vystupy\\hlidac_expedice"
LOG_DIR2 = f"{os.getenv('LOCALAPPDATA')}\\logy_vystupy\\allegro_odpovidac"
os.makedirs(LOG_DIR, exist_ok=True)

TOKEN_FILE = os.path.join(LOG_DIR2, "allegro_tokens.json")
NOTES_FILE = os.path.join(LOG_DIR, "notes.json")
WARNING_HOURS = 24 

# ==========================================
# FILTRY STAVŮ
# ==========================================
# Allegro stavy fulfillmentu - objednávky s těmito stavy se SKRYJI
ALLEGRO_SKIP_STATUSES = [
    "SENT",             # Odesláno
    "DELIVERED",        # Doručeno
    "READY_FOR_PICKUP", # Připraveno k vyzvednutí
    "PICKED_UP",        # Vyzvednuto
    "CANCELLED",        # Zrušeno
    "RETURNED",         # Vráceno
    "CLOSED",           # Ukončeno
]

# TopTrans stavy zásilky - objednávky s těmito stavy se SKRYJI
# Chceš skrýt další stav? Přidej ho sem jako nový řádek.
TOPTRANS_SKIP_STATUSES = [
    "Na rozvozovém depu",
    "Na rozvozu",
    "Doručeno",
    "Ukončená",
    "Mezi depy",
]

STATUS_TRANSLATIONS = {
    "NEW": "Nová",
    "PROCESSING": "Ve zpracování",
    "READY_FOR_SHIPMENT": "Čekající na odeslání",
    "SUSPENDED": "Pozastaveno",
    "NEZNAMY": "Neznámý"
}

# ==========================================
# SPRÁVA TOKENŮ A POZNÁMEK
# ==========================================
def load_tokens():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return {"access_token": INITIAL_ACCESS_TOKEN, "refresh_token": INITIAL_REFRESH_TOKEN}
 
def save_tokens(access_token, refresh_token):
    with open(TOKEN_FILE, "w") as f:
        json.dump({"access_token": access_token, "refresh_token": refresh_token}, f)
 
def refresh_allegro_token(refresh_token):
    auth_b64 = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    response = requests.post(
        "https://allegro.pl/auth/oauth/token",
        headers={"Authorization": f"Basic {auth_b64}"},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    response.raise_for_status()
    return response.json()
 
current_tokens = load_tokens()
save_tokens(current_tokens["access_token"], current_tokens["refresh_token"])
 
def load_notes():
    if os.path.exists(NOTES_FILE):
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}
 
def save_notes(notes):
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(notes, f, indent=4, ensure_ascii=False)
 
# ==========================================
# KOMUNIKACE S API
# ==========================================
FETCH_LIMIT       = 100
FETCH_TOTAL_MAX   = 1000 
 
def fetch_orders_from_allegro():
    global current_tokens
    url = "https://api.allegro.pl/order/checkout-forms"
    headers = {
        "Authorization": f"Bearer {current_tokens['access_token']}",
        "Accept": "application/vnd.allegro.public.v1+json"
    }
 
    all_orders = {}
    offset = 0
 
    while offset < FETCH_TOTAL_MAX:
        params = [
            ("limit", FETCH_LIMIT),
            ("offset", offset),
            ("status", "BOUGHT"),
            ("status", "FILLED_IN"),
            ("status", "READY_FOR_PROCESSING")
        ]
        response = requests.get(url, headers=headers, params=params)
 
        if response.status_code == 401:
            print("⏳ Token vypršel. Obnovuji tokeny...")
            new_data = refresh_allegro_token(current_tokens["refresh_token"])
            current_tokens["access_token"]  = new_data["access_token"]
            current_tokens["refresh_token"] = new_data["refresh_token"]
            save_tokens(current_tokens["access_token"], current_tokens["refresh_token"])
            headers["Authorization"] = f"Bearer {current_tokens['access_token']}"
            response = requests.get(url, headers=headers, params=params)
 
        response.raise_for_status()
        data = response.json()
        batch = data.get("checkoutForms", [])
        
        for order in batch:
            all_orders[order['id']] = order
 
        print(f"  📦 Staženo {len(batch)} aktivních objednávek (offset {offset})...")
        if len(batch) < FETCH_LIMIT:
            break
        offset += FETCH_LIMIT
 
    print(f"  ✅ Celkem platných objednávek v systému: {len(all_orders)}")
    return all_orders
 
def is_toptrans(order):
    try:
        name = order["delivery"]["method"]["name"] or ""
        return "toptrans" in name.lower()
    except (KeyError, TypeError):
        return False
 
def get_allegro_waybill(order_id):
    global current_tokens
    url = f"https://api.allegro.pl/order/checkout-forms/{order_id}/shipments"
    headers = {
        "Authorization": f"Bearer {current_tokens['access_token']}",
        "Accept": "application/vnd.allegro.public.v1+json"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 401:
            new_data = refresh_allegro_token(current_tokens["refresh_token"])
            current_tokens["access_token"]  = new_data["access_token"]
            current_tokens["refresh_token"] = new_data["refresh_token"]
            save_tokens(current_tokens["access_token"], current_tokens["refresh_token"])
            headers["Authorization"] = f"Bearer {current_tokens['access_token']}"
            response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            shipments = response.json().get("shipments", [])
            if shipments:
                return shipments[0].get("waybill")
    except Exception as e:
        print(f"  ⚠️  Chyba waybill {order_id}: {e}")
    return None
 
def fetch_toptrans_batch(waybills):
    url = f"{TOPTRANS_API}/order/search/"
    result = {}
    for i in range(0, len(waybills), 50):
        batch = waybills[i:i+50]
        query = ",".join(batch)
        try:
            response = requests.post(
                url, json={"orderNumber": query}, auth=(TOPTRANS_USER, TOPTRANS_PASS), timeout=15
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "ok" and data.get("data"):
                for item in data["data"]:
                    order_num = str(item.get("orderNumber", ""))
                    result[order_num] = item.get("status", "Neznámý stav")
        except Exception as e:
            print(f"  ❌ TopTrans batch chyba: {e}")
    return result
 
# ==========================================
# ZPRACOVÁNÍ DAT
# ==========================================
def categorize_orders(db_data):
    now = datetime.now(timezone.utc)
    notes_db = load_notes()
    candidates = {}
 
    for order_id, order in db_data.items():
        if order.get("status") not in ["READY_FOR_PROCESSING", "BOUGHT", "FILLED_IN"]:
            continue
            
        fulfillment = order.get("fulfillment") or {}
        fulfillment_status = str(fulfillment.get("status", "NEZNAMY")).upper()
        
        if fulfillment_status in ALLEGRO_SKIP_STATUSES:
            continue
            
        try:
            target_time_str = order["delivery"]["time"]["dispatch"]["to"]
            target_time = datetime.fromisoformat(target_time_str.replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
            
        if not target_time_str:
            continue
            
        candidates[order_id] = (order, fulfillment_status, target_time_str, target_time)
 
    toptrans_ids = [oid for oid, (o, _, _, _) in candidates.items() if is_toptrans(o)]
    print(f"  🚚 TopTrans objednávky k prohledání: {len(toptrans_ids)}")
 
    waybill_map = {}
    if toptrans_ids:
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(get_allegro_waybill, oid): oid for oid in toptrans_ids}
            for future in as_completed(futures):
                oid = futures[future]
                waybill_map[oid] = future.result()
 
    valid_waybills = [w for w in waybill_map.values() if w]
    toptrans_statuses = {}
    if valid_waybills:
        BATCH = 50
        print(f"  📡 Dotazuji TopTrans na {len(valid_waybills)} zásilek (po {BATCH})...")
        for i in range(0, len(valid_waybills), BATCH):
            batch = valid_waybills[i:i + BATCH]
            toptrans_statuses.update(fetch_toptrans_batch(batch))
        print(f"  ✅ TopTrans odpověděl na {len(toptrans_statuses)} zásilek")
 
    gui_lists = {
        "blizi_se_termin_expedice": [],
        "zpozdena_objednavka": []
    }
 
    for order_id, (order, fulfillment_status, target_time_str, target_time) in candidates.items():
        time_left = target_time - now
        cz_stav = STATUS_TRANSLATIONS.get(fulfillment_status, fulfillment_status)
 
        if is_toptrans(order):
            waybill = waybill_map.get(order_id)
            if waybill:
                stav_zasilky = toptrans_statuses.get(waybill, "Neznámý stav")
            else:
                stav_zasilky = "Bez čísla zásilky"
            if stav_zasilky in TOPTRANS_SKIP_STATUSES:
                continue
        else:
            stav_zasilky = ""
 
        buyer_login = order.get("buyer", {}).get("login", "Neznámý")
        firstName = order.get("buyer", {}).get("firstName", "Neznámý")
        lastName = order.get("buyer", {}).get("lastName", "")
        cele_jmeno = f"{firstName} {lastName}".strip()
        item_names = [item["offer"]["name"] for item in order.get("lineItems", [])]
 
        order_summary = {
            "id": order_id,
            "login": order.get("buyer", {}).get("login", ""),
            "cele_jmeno": cele_jmeno,
            "zbyva_casu": str(time_left).split(".")[0],
            "cilovy_cas_expedice": target_time_str,
            "polozky": ", ".join(item_names),
            "stav_vyrizeni": cz_stav,
            "stav_zasilky": stav_zasilky,
            "poznamka": notes_db.get(order_id, "")
        }
 
        if time_left.total_seconds() < 0:
            gui_lists["zpozdena_objednavka"].append(order_summary)
        elif time_left <= timedelta(hours=WARNING_HOURS):
            gui_lists["blizi_se_termin_expedice"].append(order_summary)
 
    return gui_lists
 
def sync_orders():
    # Stáhneme aktuální aktivní objednávky rovnou do slovníku a pošleme do GUI
    live_orders = fetch_orders_from_allegro()
    return categorize_orders(live_orders)
 
# ==========================================
# GUI ČÁST
# ==========================================
def format_time_for_gui(iso_time_str):
    try:
        clean_time_str = iso_time_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_time_str)
        dt_local = dt.astimezone() 
        return dt_local.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return iso_time_str
 
def open_order_in_browser(order_id):
    url = f"https://salescenter.allegro.com/orders/{order_id}?sellerId={SELLER_ID}"
    webbrowser.open(url)
 
def show_context_menu(event, tree):
    item = tree.identify_row(event.y)
    if item:
        tree.selection_set(item)
        tree.focus(item)
        values = tree.item(item, "values")
        order_id = str(values[0])
        login    = str(values[1])
 
        menu = tk.Menu(tree, tearoff=0)
        menu.add_command(
            label="🌐 Otevřít objednávku v prohlížeči",
            font=("Arial", 10, "bold"),
            command=lambda: open_order_in_browser(order_id)
        )
        menu.add_command(
            label=f"📋 Kopírovat login: {login}",
            font=("Arial", 10),
            command=lambda l=login: (tree.clipboard_clear(), tree.clipboard_append(l))
        )
        menu.add_command(
            label=f"📋 Kopírovat ID objednávky",
            font=("Arial", 10),
            command=lambda i=order_id: (tree.clipboard_clear(), tree.clipboard_append(i))
        )
        menu.post(event.x_root, event.y_root)
 
def edit_note_dialog(event, tree):
    item = tree.identify_row(event.y)
    if not item:
        return
    
    order_id = tree.item(item, "values")[0]
    current_note = tree.item(item, "values")[8].replace("🔴 ", "")
 
    win = tk.Toplevel()
    win.title("Upravit poznámku")
    
    x = event.x_root - 200
    y = event.y_root - 75
    win.geometry(f"400x150+{x}+{y}")
    win.resizable(False, False)
    win.grab_set()
 
    tk.Label(win, text="Poznámka k objednávce:", font=("Arial", 10)).pack(pady=(10, 2))
    entry = tk.Entry(win, font=("Arial", 11), width=45) 
    entry.insert(0, current_note)
    entry.pack(pady=5, padx=10)
    entry.focus_set()
 
    def ulozit(e=None):
        text = entry.get().strip()
        notes = load_notes()
        if text:
            notes[order_id] = text
            zobrazovany_text = f"🔴 {text}" 
        else:
            notes.pop(order_id, None)
            zobrazovany_text = ""
            
        save_notes(notes)
        
        vals = list(tree.item(item, "values"))
        vals[8] = zobrazovany_text
        tree.item(item, values=vals)
        win.destroy()
 
    btn_frame = tk.Frame(win)
    btn_frame.pack(pady=5)
    tk.Button(btn_frame, text="💾 Uložit", command=ulozit, bg="#4CAF50", fg="white",
              font=("Arial", 10, "bold"), padx=10).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_frame, text="Zrušit", command=win.destroy,
              font=("Arial", 10), padx=10).pack(side=tk.LEFT, padx=5)
    
    win.bind("<Return>", ulozit)
    win.bind("<Escape>", lambda e: win.destroy())
 
def populate_tree(tree, data_list, is_delayed=False):
    for item in tree.get_children():
        tree.delete(item)
        
    for order in data_list:
        hezky_cas = format_time_for_gui(order['cilovy_cas_expedice'])
        
        poznamka = order.get('poznamka', '')
        zobrazena_poznamka = f"🔴 {poznamka}" if poznamka else ""
        
        values = (
            order['id'],
            order.get('login', ''),
            order['cele_jmeno'],
            order['polozky'],
            order['stav_vyrizeni'],
            order.get('stav_zasilky', ''),
            hezky_cas,
            order['zbyva_casu'] if not is_delayed else "PO TERMÍNU!",
            zobrazena_poznamka
        )
        tree.insert('', tk.END, values=values)

def sort_tree_by_date(tree, col="cas", descending=True):
    """Seřadí Treeview podle sloupce s datem expedice."""
    data = []
    for item in tree.get_children(''):
        val = tree.set(item, col)
        try:
            # Formát musí odpovídat tomu, co vrací format_time_for_gui
            date_val = datetime.strptime(val, "%d.%m.%Y %H:%M")
        except ValueError:
            # Pokud chybí čas, spadne to na dno
            date_val = datetime.min 
        data.append((date_val, item))

    data.sort(reverse=descending)

    for index, (val, item) in enumerate(data):
        tree.move(item, '', index)
 
def refresh_data(trees, root):
    """Spustí načítání dat, ale UI zůstane responzivní."""
    root.title("Allegro Objednávky - ⏳ Stahuji data na pozadí...")
    
    # Dočasně vymažeme tabulky a ukážeme uživateli, že se pracuje
    for tree in trees.values():
        for item in tree.get_children():
            tree.delete(item)
        tree.insert('', tk.END, values=("", "", "⏳ Načítám data z API...", "", "", "", "", "", ""))
        
    # Spustíme stahování ve vedlejším vlákně (daemon=True zajistí, že se vlákno ukončí se zavřením aplikace)
    thread = threading.Thread(target=_fetch_data_worker, args=(trees, root), daemon=True)
    thread.start()

def _fetch_data_worker(trees, root):
    """Běží na pozadí a komunikuje s API (neblokuje GUI)."""
    try:
        gui_data = sync_orders()
        # Výsledek bezpečně pošleme zpět do hlavního (Tkinter) vlákna přes root.after()
        root.after(0, _update_gui_with_data, trees, root, gui_data, None)
    except Exception as e:
        # V případě chyby pošleme chybu do GUI
        root.after(0, _update_gui_with_data, trees, root, None, str(e))

def _update_gui_with_data(trees, root, gui_data, error):
    """Aktualizuje tabulky (Běží zpět v hlavním vlákně)."""
    if error:
        root.title(f"Chyba při aktualizaci!")
        print(f"Došlo k chybě: {error}")
        for tree in trees.values():
            for item in tree.get_children():
                tree.delete(item)
            tree.insert('', tk.END, values=("", "", f"❌ Chyba: {error}", "", "", "", "", "", ""))
        return

    # Máme data. Vyčistíme tabulky od dočasného nápisu "Načítám..."
    for tree in trees.values():
        for item in tree.get_children():
            tree.delete(item)

    # Naplníme reálná data
    populate_tree(trees["zpozdena"], gui_data["zpozdena_objednavka"], is_delayed=True)
    populate_tree(trees["blizi_se"], gui_data["blizi_se_termin_expedice"])
    
    # Rovnou tabulky seřadíme podle nejzazšího termínu (volá funkci z předchozího kroku)
    sort_tree_by_date(trees["zpozdena"], col="cas", descending=True)
    sort_tree_by_date(trees["blizi_se"], col="cas", descending=True)
    
    # Aktualizujeme titulek
    now_str = datetime.now().strftime("%H:%M:%S")
    root.title(f"Allegro Hlídač expedice (Aktualizováno: {now_str}) - Pravý klik pro otevření")


 
def create_table(parent, title, is_delayed=False):
    frame = tk.LabelFrame(parent, text=title, font=("Arial", 11, "bold"), padx=10, pady=10)
    frame.pack(fill="both", expand=True, padx=10, pady=5)
    
    columns = ("id", "login", "cele_jmeno", "polozky", "stav", "stav_zasilky", "cas", "zbyva", "poznamka")
    display_columns = ("cele_jmeno", "polozky", "stav", "stav_zasilky", "cas", "zbyva", "poznamka") if not is_delayed else ("cele_jmeno", "polozky", "stav", "stav_zasilky", "cas", "poznamka")
    tree = ttk.Treeview(frame, columns=columns, displaycolumns=display_columns, show="headings", height=8)
    
    tree.heading("cele_jmeno", text="Zákazník")
    tree.heading("polozky", text="Položky")
    tree.heading("stav", text="Stav vyřízení")
    tree.heading("stav_zasilky", text="Stav zásilky")
    tree.heading("cas", text="Termín expedice", command=lambda: sort_tree_by_date(tree, "cas", descending=True))
    tree.heading("zbyva", text="Zbývá času")
    tree.heading("poznamka", text="Poznámka")
    
    tree.column("cele_jmeno", width=150, stretch=False)
    tree.column("polozky", width=150)
    tree.column("stav", width=120, stretch=False, anchor="center")
    tree.column("stav_zasilky", width=160, stretch=False, anchor="center")
    tree.column("cas", width=120, stretch=False, anchor="center")
    tree.column("zbyva", width=100, stretch=False, anchor="center")
    tree.column("poznamka", width=200)
    
    tree.pack(fill="both", expand=True)
    
    tree.bind("<Button-3>", lambda event: show_context_menu(event, tree))
    tree.bind("<Button-2>", lambda event: show_context_menu(event, tree))
    tree.bind("<Double-1>", lambda event: edit_note_dialog(event, tree))
    
    return tree
 
def run_gui():
    root = tk.Tk()
    root.title("Allegro Objednávky - Hlídač expedice")
    root.geometry("1000x600") 
    
    style = ttk.Style()
    style.theme_use("clam")
    
    trees = {}
    
    tk.Label(root, text="ZPOŽDĚNÉ OBJEDNÁVKY", fg="red", font=("Arial", 12, "bold")).pack(pady=(10,0))
    trees["zpozdena"] = create_table(root, "", is_delayed=True)
    
    tk.Label(root, text="BLÍŽÍ SE TERMÍN EXPEDICE", fg="orange", font=("Arial", 12, "bold")).pack(pady=(10,0))
    trees["blizi_se"] = create_table(root, "")
    
    refresh_btn = tk.Button(root, text="🔄 Aktualizovat data z Allegra", 
                            font=("Arial", 12, "bold"), bg="#4CAF50", fg="white",
                            command=lambda: refresh_data(trees, root),
                            padx=20, pady=10)
    refresh_btn.pack(pady=10)
    
    refresh_data(trees, root)
    root.mainloop()
 
if __name__ == "__main__":
    run_gui()