import os
import sys
import boto3
import json
import copy
import uuid
import ctypes
import threading
from pinecone import Pinecone
from datetime import datetime, timezone
from thefuzz import fuzz, process
import tkinter as tk
from openai import OpenAI
from dotenv import load_dotenv
from tkinter import filedialog, messagebox, scrolledtext, ttk
from datetime import date
from pathlib import Path
import pandas as pd
import pdfplumber

#--optional libraries for using deepseek via ollama
# import re
# import ollama
# import requests
# import sv_ttk
# import darkdetect
#--optional libraries for using deepseek via ollama

ctypes.windll.shcore.SetProcessDpiAwareness(1)

if hasattr(sys, '_MEIPASS'):
    CONFIG_PATH = Path(sys.executable).parent / "config.json"
else:
    CONFIG_PATH = Path(__file__).parent / "config.json"

BASE_ENTRY = {
    "id": None,
    "title": None,
    "category": None,
    "type": None,
    "source": None,
    "date_of_event": None,
    "date_added": str(date.today()),
    "location": {
        "description": None,
        "city": None,
        "state": None,
        "country": "USA",
        "coordinates": None
    },
    "witness_count": None,
    "physical_evidence": None,
    "summary": None,
    "tags": [],
    "raw_text": None,
    "embed_text": None,
    "notes": None
}

COUNTRY_NORMALIZE = {
    "united states": "USA",
    "united states of america": "USA",
    "us": "USA",
    "u.s.": "USA",
    "u.s.a.": "USA",
}

JUNK_VALUES = {"unknown", "none", "n/a", "null", "nan", ""}

STATE_ABBREV = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "British Columbia": "BC", "Ontario": "ON",
}

FIELD_SYNONYMS = {
    "summary":       ["observed", "description", "report", "sighting", "details", "account", "narrative", "text", "what_happened"],
    "title":         ["title", "name", "report_title", "heading", "subject"],
    "state":         ["state", "st", "state_name", "location_state", "province", "region", "canton"],
    "city":          ["city", "town", "municipality", "city_name", "commune", "borough", "district"],
    "date_of_event": ["date", "date_of_event", "event_date", "year", "occurred", "incident_date", "timestamp"],
    "latitude":      ["latitude", "lat", "y"],
    "longitude":     ["longitude", "lon", "long", "x"],
    "county":        ["county", "county_name", "parish"],
    "country":       ["country", "country_name", "nation", "location_country"],
    "category":      ["category", "type", "classification", "class"],
    "number":        ["number", "id", "report_id", "report_number", "case_number"],
    "location":      ["location", "location_details", "location_description", "place", "address"],
}

openai_client  = None
dynamodb       = None
s3_client      = None
pinecone_index = None

S3_BUCKET           = "compendium-raw-files"
DYNAMO_TABLE        = "compendium-entries"
PINECONE_INDEX_NAME = "compendium"

# def summarize_with_deepseek(text: str, target_chars: int = 800) -> str:
#     try:
#         response = ollama.chat(
#             model="deepseek-r1:14b",
#             messages=[
#                 {
#                     "role": "user",
#                     "content": f"Summarize the following text in {target_chars} characters or less. Return only the summary, no preamble:\n\n{text}"
#                 }
#             ]
#         )
#         content = response["message"]["content"]
#         content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
#         return content
#     except Exception as e:
#         log_message(f"DeepSeek error: {e}")
#         return text[:target_chars]

def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}

def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

def get_categories():
    config = load_config()
    cats = config.get("categories", ["UFO", "Missing411", "Cryptid", "Abduction"])
    return cats + ["Other (Enter Below)"]

def init_clients():
    global openai_client, dynamodb, s3_client, pinecone_index

    config   = load_config()
    env_path = config.get("env_path")

    if env_path and Path(env_path).exists():
        load_dotenv(dotenv_path=env_path, override=True)
    else:
        log_message("WARNING: No env file configured — go to Settings to set it up")
        return

    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        openai_client = OpenAI(api_key=api_key)
        log_message("OpenAI client initialised")
    else:
        log_message("WARNING: OPENAI_API_KEY not found in env file")

    aws_key    = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
    aws_region = os.getenv("AWS_REGION", "us-east-1")
    if aws_key and aws_secret:
        session   = boto3.Session(
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
            region_name=aws_region
        )
        dynamodb  = session.resource("dynamodb")
        s3_client = session.client("s3")
        log_message("AWS clients initialised")
    else:
        log_message("WARNING: AWS credentials not found in env file")

    pc_key = os.getenv("PINECONE_API_KEY")
    if pc_key:
        pc = Pinecone(api_key=pc_key)
        pinecone_index = pc.Index(PINECONE_INDEX_NAME)
        log_message("Pinecone client initialised")
    else:
        log_message("WARNING: PINECONE_API_KEY not found in env file")

def show_settings():
    win = tk.Toplevel()
    win.iconbitmap(resource_path("all_seeing_eye.ico"))
    win.title("Settings")
    win.grab_set()
    win.resizable(False, False)

    config = load_config()

    ttk.Label(win, text="Settings", font=("Segoe UI", 11, "bold")).grid(
        row=0, column=0, columnspan=3, padx=16, pady=(16, 4), sticky="w")
    ttk.Separator(win, orient="horizontal").grid(
        row=1, column=0, columnspan=3, sticky="ew", padx=16, pady=4)

    # Env file
    ttk.Label(win, text="Env file path:").grid(row=2, column=0, padx=16, pady=8, sticky="w")
    env_var = tk.StringVar(value=config.get("env_path", ""))
    ttk.Entry(win, textvariable=env_var, width=40).grid(row=2, column=1, padx=8, pady=8)
    ttk.Button(win, text="Browse", command=lambda: env_var.set(
        filedialog.askopenfilename(
            title="Select env file",
            filetypes=[("Env files", "*.env"), ("All files", "*.*")]
        ) or env_var.get()
    )).grid(row=2, column=2, padx=(0, 16), pady=8)

    ttk.Separator(win, orient="horizontal").grid(
        row=3, column=0, columnspan=3, sticky="ew", padx=16, pady=4)

    # Output folder
    ttk.Label(win, text="Local export folder:").grid(row=4, column=0, padx=16, pady=8, sticky="w")
    output_var = tk.StringVar(value=config.get("output_dir", ""))
    ttk.Entry(win, textvariable=output_var, width=40).grid(row=4, column=1, padx=8, pady=8)
    ttk.Button(win, text="Browse", command=lambda: output_var.set(
        filedialog.askdirectory(title="Select output folder") or output_var.get()
    )).grid(row=4, column=2, padx=(0, 16), pady=8)

    ttk.Separator(win, orient="horizontal").grid(
        row=5, column=0, columnspan=3, sticky="ew", padx=16, pady=4)

    # Categories
    ttk.Label(win, text="Categories:", font=("Segoe UI", 9, "bold")).grid(
        row=6, column=0, columnspan=3, padx=16, pady=(8, 4), sticky="w")

    cat_frame = ttk.Frame(win)
    cat_frame.grid(row=7, column=0, columnspan=3, padx=16, sticky="ew", pady=(0, 8))

    current_cats = config.get("categories", ["UFO", "Missing411", "Cryptid", "Abduction"])

    cat_listbox = tk.Listbox(cat_frame, height=6, width=30, selectmode=tk.SINGLE)
    cat_listbox.pack(side="left", fill="y")

    for cat in current_cats:
        cat_listbox.insert(tk.END, cat)

    cat_btn_frame = ttk.Frame(cat_frame)
    cat_btn_frame.pack(side="left", padx=(8, 0), anchor="n")

    new_cat_var = tk.StringVar()
    ttk.Entry(cat_btn_frame, textvariable=new_cat_var, width=20).pack(pady=(0, 4))

    def add_category():
        val = new_cat_var.get().strip()
        if not val:
            return
        existing = list(cat_listbox.get(0, tk.END))
        if val in existing:
            return
        cat_listbox.insert(tk.END, val)
        new_cat_var.set("")

    def delete_category():
        sel = cat_listbox.curselection()
        if sel:
            cat_listbox.delete(sel[0])

    ttk.Button(cat_btn_frame, text="Add", command=add_category).pack(fill="x", pady=(0, 4))
    ttk.Button(cat_btn_frame, text="Delete selected", command=delete_category).pack(fill="x")

    ttk.Separator(win, orient="horizontal").grid(
        row=8, column=0, columnspan=3, sticky="ew", padx=16, pady=8)

    def on_save():
        config["env_path"]   = env_var.get()
        config["output_dir"] = output_var.get()
        config["categories"] = list(cat_listbox.get(0, tk.END))
        save_config(config)
        log_message("Settings saved")
        win.destroy()
        init_clients()
        refresh_category_dropdown()

    footer = ttk.Frame(win)
    footer.grid(row=9, column=0, columnspan=3, padx=16, pady=(0, 16), sticky="e")
    ttk.Button(footer, text="Cancel", command=win.destroy).pack(side="right", padx=(8, 0))
    ttk.Button(footer, text="Save", command=on_save).pack(side="right")

def summarize_with_openai(text: str, target_chars: int = 800) -> str:
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": f"Summarize the following text in {target_chars} characters or less. Return only the summary, no preamble:\n\n{text}"
                }
            ]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        log_message(f"OpenAI summarization error: {e}")
        return text[:target_chars]

def embed_text_with_openai(text: str) -> list[float]:
    response = openai_client.embeddings.create(
        input=text,
        model="text-embedding-3-small"
    )
    return response.data[0].embedding

#--Initial column mapping to JSON--#
def detect_column_mapping(columns, threshold=80):
    mapping = {}
    claimed_fields = set()
    unmatched = []

    for col in columns:
        best_field = None
        best_score = 0

        for field, synonyms in FIELD_SYNONYMS.items():
            if field in claimed_fields:
                continue
            match, score = process.extractOne(col.lower(), synonyms)
            if score >= threshold and score > best_score:
                best_field = field
                best_score = score

        if best_field:
            mapping[col] = best_field
            claimed_fields.add(best_field)
        else:
            unmatched.append(col)

    return mapping, unmatched

EMBED_PRICE_PER_M  = 0.02    # text-embedding-3-small, $ per 1M tokens
SUMM_INPUT_PER_M   = 0.15    # gpt-4o-mini input,      $ per 1M tokens
SUMM_OUTPUT_PER_M  = 0.075   # gpt-4o-mini output,     $ per 1M tokens
SUMMARY_THRESHOLD  = 1000    # chars — triggers summarisation call
SUMMARY_TARGET     = 800     # chars — output target
METADATA_OVERHEAD  = 100     # chars — title/location/tags added to embed_text
CHARS_PER_TOKEN    = 4       # rough approximation

def estimate_costs(texts: list[str]) -> dict:
    """
    Given a list of raw summary strings, estimate token counts and USD cost
    for one full pipeline run (summarisation + embedding).
    """
    total_rows        = len(texts)
    long_rows         = [t for t in texts if len(str(t)) > SUMMARY_THRESHOLD]
    short_rows        = [t for t in texts if len(str(t)) <= SUMMARY_THRESHOLD]

    # Summarisation tokens (only long rows)
    summ_input_chars  = sum(len(str(t)) for t in long_rows)
    summ_output_chars = len(long_rows) * SUMMARY_TARGET
    summ_input_tok    = summ_input_chars  / CHARS_PER_TOKEN
    summ_output_tok   = summ_output_chars / CHARS_PER_TOKEN

    # Embedding tokens — short rows use full text, long rows capped at target + overhead
    embed_chars = (
        sum(min(len(str(t)), SUMMARY_TARGET) + METADATA_OVERHEAD for t in long_rows)
        + sum(len(str(t)) + METADATA_OVERHEAD for t in short_rows)
    )
    embed_tok = embed_chars / CHARS_PER_TOKEN

    # Costs
    summ_cost  = (summ_input_tok / 1_000_000 * SUMM_INPUT_PER_M
                  + summ_output_tok / 1_000_000 * SUMM_OUTPUT_PER_M)
    embed_cost = embed_tok / 1_000_000 * EMBED_PRICE_PER_M
    total_cost = summ_cost + embed_cost

    return {
        "total_rows":      total_rows,
        "long_rows":       len(long_rows),
        "short_rows":      len(short_rows),
        "summ_input_tok":  int(summ_input_tok),
        "summ_output_tok": int(summ_output_tok),
        "embed_tok":       int(embed_tok),
        "summ_cost":       summ_cost,
        "embed_cost":      embed_cost,
        "total_cost":      total_cost,
    }

def show_mapping_ui(columns, auto_mapping, file_path, df, on_complete):
    win = tk.Toplevel()
    win.iconbitmap(resource_path("all_seeing_eye.ico"))
    win.title("Map Columns")
    win.grab_set()
    win.resizable(True, True)
    win.geometry("1280x900")
    win.minsize(1800, 700)

    schema_options = [
        "— ignore —", "summary", "title", "state", "city", "date_of_event",
        "latitude", "longitude", "county", "category", "number", "location", "country"
    ]

    row_vars = {}
    current_row_idx = [0]  # mutable container so nested functions can modify it
    total_rows = len(df)

    # Pre-scan column text for cost estimation
    col_texts = {col: df[col].dropna().astype(str).tolist() for col in columns}

    # --- header ---
    header_frame = ttk.Frame(win)
    header_frame.pack(fill="x", padx=16, pady=(16, 4))
    ttk.Label(header_frame, text="Map columns to schema fields",
              font=("Segoe UI", 11, "bold")).pack(anchor="w")
    ttk.Label(header_frame,
              text=f"{Path(file_path).name} — {len(columns)} columns detected",
              foreground="gray").pack(anchor="w")

    ttk.Separator(win, orient="horizontal").pack(fill="x", padx=16, pady=4)

    # --- footer (packed first so it's always visible) ---
    ttk.Separator(win, orient="horizontal").pack(side="bottom", fill="x", padx=16, pady=(4, 0))

    footer = ttk.Frame(win)
    footer.pack(side="bottom", fill="x", padx=16, pady=(0, 16))

    # --- main horizontal split ---
    main_frame = ttk.Frame(win)
    main_frame.pack(fill="both", expand=True, padx=16, pady=(0, 4))
    main_frame.columnconfigure(0, weight=2)
    main_frame.columnconfigure(1, weight=3)
    main_frame.rowconfigure(0, weight=1)

    # ── LEFT SIDE ──────────────────────────────────────────────────────────
    left_frame = ttk.Frame(main_frame)
    left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    left_frame.rowconfigure(1, weight=1)
    left_frame.columnconfigure(0, weight=1)

    # column headers
    col_header = ttk.Frame(left_frame)
    col_header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
    ttk.Label(col_header, text="Dataset column", width=22, foreground="gray").grid(row=0, column=0, sticky="w")
    ttk.Label(col_header, text="Maps to",        width=20, foreground="gray").grid(row=0, column=1, padx=8, sticky="w")
    ttk.Label(col_header, text="raw_text",        width=8, foreground="gray").grid(row=0, column=2, padx=4)
    ttk.Label(col_header, text="notes",           width=6, foreground="gray").grid(row=0, column=3, padx=4)

    # scrollable rows
    scroll_container = ttk.Frame(left_frame)
    scroll_container.grid(row=1, column=0, sticky="nsew")

    canvas       = tk.Canvas(scroll_container, highlightthickness=0)
    scrollbar    = ttk.Scrollbar(scroll_container, orient="vertical", command=canvas.yview)
    scroll_frame = ttk.Frame(canvas)

    scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    update_cost_display   = lambda: None
    update_continue_state = lambda: None
    update_preview        = lambda: None

    for col in columns:
        auto_field = auto_mapping.get(col, "— ignore —")
        is_auto    = col in auto_mapping

        field_var = tk.StringVar(value=auto_field)
        raw_var   = tk.BooleanVar(value=False)
        notes_var = tk.BooleanVar(value=False)

        row_frame = ttk.Frame(scroll_frame)
        row_frame.pack(fill="x", pady=2)

        lbl = ttk.Label(row_frame, text=col, width=22)
        lbl.grid(row=0, column=0, sticky="w")
        if is_auto:
            lbl.configure(foreground="#2e7d32")

        dropdown = ttk.Combobox(row_frame, textvariable=field_var,
                                values=schema_options, state="readonly", width=18)
        dropdown.grid(row=0, column=1, padx=8)

        def on_dropdown_change(e, col=col, field_var=field_var, raw_var=raw_var):
            if field_var.get() == "— ignore —":
                raw_var.set(True)
            else:
                raw_var.set(False)
            update_cost_display()
            update_preview()

        dropdown.bind("<<ComboboxSelected>>", on_dropdown_change)

        ttk.Checkbutton(row_frame, variable=raw_var).grid(row=0, column=2, padx=12)
        ttk.Checkbutton(row_frame, variable=notes_var).grid(row=0, column=3, padx=8)

        row_vars[col] = {"field": field_var, "raw_text": raw_var, "notes": notes_var}

        if auto_field == "— ignore —":
            raw_var.set(True)

    # cost estimation panel
    ttk.Separator(left_frame, orient="horizontal").grid(row=2, column=0, sticky="ew", pady=(8, 4))

    cost_frame = ttk.Frame(left_frame)
    cost_frame.grid(row=3, column=0, sticky="ew")

    ttk.Label(cost_frame, text="Estimated run cost",
              font=("Segoe UI", 9, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))

    labels = [
        "Rows:",
        "Need summarisation (>1k chars):",
        "Summarisation tokens:",
        "Embedding tokens:",
        "Est. total cost (USD):",
    ]
    for i, text in enumerate(labels):
        ttk.Label(cost_frame, text=text, foreground="gray", width=32).grid(row=i+1, column=0, sticky="w")

    cost_rows_var  = tk.StringVar(value="—")
    cost_long_var  = tk.StringVar(value="—")
    cost_summ_var  = tk.StringVar(value="—")
    cost_embed_var = tk.StringVar(value="—")
    cost_total_var = tk.StringVar(value="—")
    cost_note_var  = tk.StringVar(value="Select the summary column above to estimate cost")

    ttk.Label(cost_frame, textvariable=cost_rows_var).grid(row=1, column=1, sticky="w", padx=8)
    ttk.Label(cost_frame, textvariable=cost_long_var).grid(row=2, column=1, sticky="w", padx=8)
    ttk.Label(cost_frame, textvariable=cost_summ_var).grid(row=3, column=1, sticky="w", padx=8)
    ttk.Label(cost_frame, textvariable=cost_embed_var).grid(row=4, column=1, sticky="w", padx=8)
    ttk.Label(cost_frame, textvariable=cost_total_var,
              font=("Segoe UI", 9, "bold")).grid(row=5, column=1, sticky="w", padx=8)
    ttk.Label(cost_frame, textvariable=cost_note_var,
              foreground="gray", font=("Segoe UI", 8)).grid(row=6, column=0, columnspan=2, sticky="w", pady=(2, 0))

    # destination checkboxes
    ttk.Separator(left_frame, orient="horizontal").grid(row=4, column=0, sticky="ew", pady=(8, 4))

    dest_frame = ttk.Frame(left_frame)
    dest_frame.grid(row=5, column=0, sticky="ew")

    ttk.Label(dest_frame, text="Destination",
              font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))

    compendium_var   = tk.BooleanVar(value=True)
    local_export_var = tk.BooleanVar(value=False)

    ttk.Checkbutton(dest_frame, text="Upload to Compendium",
                    variable=compendium_var,
                    command=update_continue_state).pack(anchor="w")
    ttk.Checkbutton(dest_frame, text="Local Export (JSON)",
                    variable=local_export_var,
                    command=update_continue_state).pack(anchor="w")

    local_warn_var = tk.StringVar(value="")
    ttk.Label(dest_frame, textvariable=local_warn_var,
              foreground="#b71c1c", font=("Segoe UI", 8)).pack(anchor="w")

    # ── RIGHT SIDE — JSON preview ──────────────────────────────────────────
    right_frame = ttk.Frame(main_frame, relief="flat")
    right_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
    right_frame.rowconfigure(1, weight=1)
    right_frame.columnconfigure(0, weight=1)

    # preview header + row cycler
    preview_header = ttk.Frame(right_frame)
    preview_header.grid(row=0, column=0, sticky="ew", pady=(0, 4))

    ttk.Label(preview_header, text="Entry preview",
              font=("Segoe UI", 9, "bold")).pack(side="left")

    row_counter_var = tk.StringVar(value=f"Row 1 of {total_rows:,}")
    ttk.Label(preview_header, textvariable=row_counter_var,
              foreground="gray", font=("Segoe UI", 8)).pack(side="right", padx=(0, 4))
    ttk.Button(preview_header, text="▶", width=2,
               command=lambda: cycle_row(1)).pack(side="right")
    ttk.Button(preview_header, text="◀", width=2,
               command=lambda: cycle_row(-1)).pack(side="right", padx=(0, 2))

    # preview text widget
    preview_text = tk.Text(
        right_frame,
        font=("Consolas", 9),
        wrap="word",
        state=tk.DISABLED,
        relief="flat",
        borderwidth=1,
        highlightthickness=1,
    )
    preview_text.grid(row=1, column=0, sticky="nsew")
    preview_scroll = ttk.Scrollbar(right_frame, orient="vertical", command=preview_text.yview)
    preview_scroll.grid(row=1, column=1, sticky="ns")
    preview_text.configure(yscrollcommand=preview_scroll.set)

    # colour tags
    preview_text.tag_configure("key",      foreground="#555555")
    preview_text.tag_configure("mapped",   foreground="#2e7d32")
    preview_text.tag_configure("unmapped", foreground="#aaaaaa")
    preview_text.tag_configure("source",   foreground="#1565c0", font=("Consolas", 9, "bold"))
    preview_text.tag_configure("value",    foreground="#555555", font=("Consolas", 9, "italic"))

    # ── preview logic ───────────────────────────────────────────────────────

    # All BASE_ENTRY fields we want to show in order
    PREVIEW_FIELDS = [
        "id", "title", "category", "type", "source",
        "date_of_event", "date_added",
        "location.description", "location.city", "location.state", "location.country", "location.coordinates",
        "witness_count", "physical_evidence",
        "summary", "tags", "raw_text", "embed_text", "notes"
    ]

    def get_field_mapping():
        """Return dict of schema_field → (col_name, sample_value) for current row."""
        row = df.iloc[current_row_idx[0]]
        result = {}
        for col, vars_ in row_vars.items():
            field = vars_["field"].get()
            if field and field != "— ignore —":
                val = safe_str(row.get(col)) or ""
                # Truncate long values for preview
                display_val = val[:120] + "..." if len(val) > 120 else val
                result[field] = (col, display_val)
        return result

    def update_preview():
        mapping = get_field_mapping()

        # Title fallback preview
        if "title" not in mapping:
            loc_desc = mapping.get("description", ("", ""))[1] if "description" in mapping else ""
            state = mapping.get("state", ("", ""))[1] if "state" in mapping else ""
            parts = [p for p in [loc_desc, state] if p]
            if parts:
                mapping["title"] = ("(generated)", " — ".join(parts))

        preview_text.config(state=tk.NORMAL)
        preview_text.delete("1.0", tk.END)

        preview_text.insert(tk.END, "{\n", "key")

        for field in PREVIEW_FIELDS:
            # Handle nested location fields
            display_key = field.replace("location.", "  ") if field.startswith("location.") else field
            schema_key  = field.split(".")[-1] if "." in field else field

            # Special indent for location sub-fields
            if field.startswith("location."):
                if field == "location.description":
                    preview_text.insert(tk.END, '  "location": {\n', "key")
                indent = "    "
            else:
                indent = "  "

            if schema_key in mapping:
                col_name, sample = mapping[schema_key]
                preview_text.insert(tk.END, f'{indent}"{schema_key}": ', "key")
                preview_text.insert(tk.END, f"[{col_name}] ", "source")
                preview_text.insert(tk.END, f'"{sample}"\n', "mapped")
            else:
                preview_text.insert(tk.END, f'{indent}"{schema_key}": ', "key")
                preview_text.insert(tk.END, "null\n", "unmapped")

            if field == "location.coordinates":
                preview_text.insert(tk.END, "  },\n", "key")

        preview_text.insert(tk.END, "}", "key")
        preview_text.config(state=tk.DISABLED)

    def cycle_row(direction):
        current_row_idx[0] = (current_row_idx[0] + direction) % total_rows
        row_counter_var.set(f"Row {current_row_idx[0] + 1} of {total_rows:,}")
        update_preview()

    ttk.Label(footer, text="● Auto-mapped by detection", foreground="#2e7d32").pack(side="left")

    continue_btn = ttk.Button(footer, text="Save & continue",
                              command=lambda: on_confirm(), state=tk.DISABLED)
    continue_btn.pack(side="right")
    ttk.Button(footer, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))

    # ── real function definitions ───────────────────────────────────────────

    def update_cost_display():
        summary_col = next(
            (col for col, v in row_vars.items() if v["field"].get() == "summary"),
            None
        )
        if summary_col is None:
            for v in (cost_rows_var, cost_long_var, cost_summ_var, cost_embed_var, cost_total_var):
                v.set("—")
            cost_note_var.set("Select the summary column above to estimate cost")
            update_continue_state()
            return

        texts = col_texts.get(summary_col, [])
        est   = estimate_costs(texts)
        pct   = round(est["long_rows"] / est["total_rows"] * 100, 1) if est["total_rows"] else 0

        cost_rows_var.set(f"{est['total_rows']:,}")
        cost_long_var.set(f"{est['long_rows']:,}  ({pct}%)")
        cost_summ_var.set(f"{est['summ_input_tok']:,} in / {est['summ_output_tok']:,} out")
        cost_embed_var.set(f"{est['embed_tok']:,}")
        cost_total_var.set(f"${est['total_cost']:.4f}")
        cost_note_var.set("~4 chars/token · gpt-4o-mini + text-embedding-3-small pricing")
        update_continue_state()

    def update_continue_state(*_):
        config          = load_config()
        output_dir      = config.get("output_dir", "")
        want_local      = local_export_var.get()
        want_compendium = compendium_var.get()
        has_output_dir  = bool(output_dir and Path(output_dir).exists())

        if want_local and not has_output_dir:
            local_warn_var.set("No export folder configured — set one in Settings")
        else:
            local_warn_var.set("")

        if not want_compendium and not want_local:
            continue_btn.config(state=tk.DISABLED)
        elif want_local and not has_output_dir:
            continue_btn.config(state=tk.DISABLED)
        else:
            continue_btn.config(state=tk.NORMAL)

    def on_confirm():
        global openai_client, dynamodb, s3_client, pinecone_index
        if compendium_var.get():
            if not all([openai_client, dynamodb, s3_client, pinecone_index]):
                log_message("ERROR: One or more clients not initialized — check Settings and verify your env file has OPENAI_API_KEY, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and PINECONE_API_KEY")
                return

        final_mapping = {}
        for col, vars_ in row_vars.items():
            field = vars_["field"].get()
            final_mapping[col] = {
                "field":    field if field != "— ignore —" else None,
                "raw_text": vars_["raw_text"].get(),
                "notes":    vars_["notes"].get(),
            }

        cache_path = Path(file_path).parent / f"{Path(file_path).stem}_mapping.json"
        with open(cache_path, "w") as f:
            json.dump(final_mapping, f, indent=2)
        log_message(f"Mapping saved to {cache_path.name}")

        destinations = {
            "compendium":   compendium_var.get(),
            "local_export": local_export_var.get(),
        }

        win.destroy()
        on_complete(final_mapping, destinations)

    # Initial render
    update_continue_state()
    update_preview()

def show_pdf_metadata_ui(raw_text, file_path, on_complete):
    win = tk.Toplevel()
    win.iconbitmap(resource_path("all_seeing_eye.ico"))
    win.title("PDF Entry Details")
    win.grab_set()
    win.resizable(False, False)

    categories = ["Bigfoot", "UFO", "Missing411", "Cryptid", "Abduction"]

    ttk.Label(win, text="PDF Entry Details", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=2, padx=16, pady=(16, 4), sticky="w")
    ttk.Label(win, text=Path(file_path).name, foreground="gray").grid(row=1, column=0, columnspan=2, padx=16, pady=(0, 12), sticky="w")

    ttk.Separator(win, orient="horizontal").grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=4)

    ttk.Label(win, text="Title").grid(row=3, column=0, padx=16, pady=4, sticky="w")
    title_var = tk.StringVar()
    ttk.Entry(win, textvariable=title_var, width=40).grid(row=3, column=1, padx=16, pady=4)

    ttk.Label(win, text="Category").grid(row=4, column=0, padx=16, pady=4, sticky="w")
    category_var = tk.StringVar(value=get_category())
    ttk.Combobox(win, textvariable=category_var, values=categories, state="readonly", width=38).grid(row=4, column=1, padx=16, pady=4)

    ttk.Label(win, text="Date of event").grid(row=5, column=0, padx=16, pady=4, sticky="w")
    date_var = tk.StringVar()
    ttk.Entry(win, textvariable=date_var, width=40).grid(row=5, column=1, padx=16, pady=4)
    ttk.Label(win, text="YYYY-MM-DD or leave blank", foreground="gray", font=("Segoe UI", 9)).grid(row=6, column=1, padx=16, sticky="w")

    ttk.Label(win, text="Location").grid(row=7, column=0, padx=16, pady=4, sticky="w")
    location_var = tk.StringVar()
    ttk.Entry(win, textvariable=location_var, width=40).grid(row=7, column=1, padx=16, pady=4)

    ttk.Label(win, text="State").grid(row=8, column=0, padx=16, pady=4, sticky="w")
    state_var = tk.StringVar()
    ttk.Combobox(win, textvariable=state_var, values=sorted(STATE_ABBREV.values()), state="readonly", width=38).grid(row=8, column=1, padx=16, pady=4)

    ttk.Separator(win, orient="horizontal").grid(row=9, column=0, columnspan=2, sticky="ew", padx=16, pady=8)

    # --- cost estimate ---
    pdf_est = estimate_costs([raw_text])
    cost_text = (
        f"Est. cost:  "
        f"{'Summarisation + embedding' if pdf_est['long_rows'] else 'Embedding only'}  —  "
        f"${pdf_est['total_cost']:.4f}"
    )
    ttk.Label(win, text=cost_text, foreground="gray",
              font=("Segoe UI", 8)).grid(row=10, column=0, columnspan=2, padx=16, sticky="w")

    ttk.Separator(win, orient="horizontal").grid(row=11, column=0, columnspan=2, sticky="ew", padx=16, pady=8)

    # --- destination checkboxes ---
    dest_frame = ttk.Frame(win)
    dest_frame.grid(row=12, column=0, columnspan=2, padx=16, sticky="w", pady=(0, 4))

    ttk.Label(dest_frame, text="Destination",
              font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))

    compendium_var   = tk.BooleanVar(value=True)
    local_export_var = tk.BooleanVar(value=False)

    ttk.Checkbutton(dest_frame, text="Upload to Compendium",
                    variable=compendium_var,
                    command=lambda: update_pdf_btn()).pack(anchor="w")
    ttk.Checkbutton(dest_frame, text="Local Export (JSON)",
                    variable=local_export_var,
                    command=lambda: update_pdf_btn()).pack(anchor="w")

    pdf_warn_var = tk.StringVar(value="")
    ttk.Label(dest_frame, textvariable=pdf_warn_var,
              foreground="#b71c1c", font=("Segoe UI", 8)).pack(anchor="w")

    ttk.Separator(win, orient="horizontal").grid(row=13, column=0, columnspan=2, sticky="ew", padx=16, pady=8)

    # --- footer ---
    footer = ttk.Frame(win)
    footer.grid(row=14, column=0, columnspan=2, padx=16, pady=(0, 16), sticky="e")
    ttk.Button(footer, text="Cancel", command=win.destroy).pack(side="right", padx=(8, 0))
    confirm_btn = ttk.Button(footer, text="Confirm & write entry", command=lambda: on_confirm())
    confirm_btn.pack(side="right")

    def update_pdf_btn():
        config         = load_config()
        output_dir     = config.get("output_dir", "")
        want_local     = local_export_var.get()
        want_compendium = compendium_var.get()
        has_output_dir = bool(output_dir and Path(output_dir).exists())

        if want_local and not has_output_dir:
            pdf_warn_var.set("No export folder configured — set one in Settings")
        else:
            pdf_warn_var.set("")

        if not want_compendium and not want_local:
            confirm_btn.config(state=tk.DISABLED)
        elif want_local and not has_output_dir:
            confirm_btn.config(state=tk.DISABLED)
        else:
            confirm_btn.config(state=tk.NORMAL)

    def on_confirm():
        global openai_client, dynamodb, s3_client, pinecone_index
        if not title_var.get().strip():
            log_message("Title is required for PDF entries")
            return

        if compendium_var.get():
            if not all([openai_client, dynamodb, s3_client, pinecone_index]):
                log_message("ERROR: One or more clients not initialized — check Settings")
                return

        entry = copy.deepcopy(BASE_ENTRY)
        entry["id"]            = str(uuid.uuid4())
        entry["title"]         = safe_str(title_var.get())
        entry["category"]      = category_var.get()
        entry["type"]          = "account"
        entry["source"]        = Path(file_path).name
        entry["date_of_event"] = safe_str(date_var.get()) or "unknown"
        entry["date_added"]    = str(date.today())
        entry["summary"]       = raw_text
        entry["raw_text"]      = raw_text
        entry["location"] = {
            "description": safe_str(location_var.get()),
            "city":        None,
            "state":       state_var.get() or None,
            "country":     "US",
            "coordinates": None
        }
        entry["embed_text"] = build_embed_text(entry)

        destinations = {
            "compendium":   compendium_var.get(),
            "local_export": local_export_var.get(),
        }

        win.destroy()
        on_complete(entry, destinations)

    update_pdf_btn()

def map_rows(df, final_mapping, file_path, category, destinations):

    entries = []
    skipped = 0
    title_counts = {}

    for _, row in df.iterrows():
        try:
            entry = copy.deepcopy(BASE_ENTRY)

            for col, mapping in final_mapping.items():
                field = mapping.get("field")
                val   = safe_str(row.get(col))

                if field == "summary":
                    entry["summary"] = val
                elif field == "title":
                    entry["title"] = val
                elif field == "state":
                    entry["location"]["state"] = STATE_ABBREV.get(val, val)
                elif field == "city":
                    entry["location"]["city"] = val
                elif field == "date_of_event":
                    if val:
                        parsed = pd.to_datetime(val, errors="coerce")
                        entry["date_of_event"] = parsed.date().isoformat() if pd.notna(parsed) else val
                elif field == "latitude":
                    entry["_lat"] = safe_float(row.get(col))
                elif field == "longitude":
                    entry["_lon"] = safe_float(row.get(col))
                elif field == "county":
                    entry["location"]["description"] = val
                elif field == "category":
                    entry["notes"] = val
                elif field == "number":
                    entry["id"] = f"{Path(file_path).stem}_{val}" if val else str(uuid.uuid4())
                elif field == "location":
                    entry["location"]["description"] = val
                elif field == "country":
                    entry["location"]["country"] = val

            lat = entry.pop("_lat", None)
            lon = entry.pop("_lon", None)
            if lat and lon:
                entry["location"]["coordinates"] = f"{lat},{lon}"

            raw_parts = [
                f"{col}: {safe_str(row.get(col))}"
                for col, m in final_mapping.items()
                if m.get("raw_text") and safe_str(row.get(col))
            ]
            entry["raw_text"] = "\n".join(raw_parts)

            notes_parts = [
                f"{col}: {safe_str(row.get(col))}"
                for col, m in final_mapping.items()
                if m.get("notes") and safe_str(row.get(col))
            ]
            entry["notes"] = "\n".join(notes_parts) if notes_parts else entry.get("notes")

            entry["category"]   = category
            entry["type"]       = "account"
            entry["source"]     = Path(file_path).name
            entry["date_added"] = str(date.today())
            if not entry.get("title"):
                loc = entry.get("location", {})
                parts = []
                if loc.get("description"):
                    parts.append(loc["description"])
                if loc.get("state"):
                    parts.append(loc["state"])
                base_title = " — ".join(parts) if parts else "Untitled"
                count = title_counts.get(base_title, 0) + 1
                title_counts[base_title] = count
                entry["title"] = base_title if count == 1 else f"{base_title} ({count})"
            entry["embed_text"] = build_embed_text(entry)

            entries.append(entry)

        except Exception as e:
            log_message(f"Skipped row: {e}")
            root.update_idletasks()
            skipped += 1

    log_message(f"Rows built: {len(entries)}, skipped: {skipped}")
    try:
        process_and_deliver(entries, Path(file_path).stem, destinations)
    except Exception as e:
        log_message(f"process_and_deliver error: {e}")

def write_entry(entry):
    output_dir = Path("C:/Users/agood/Desktop/output")
    output_dir.mkdir(exist_ok=True)
    entry_id = entry.get("id") or str(uuid.uuid4())
    out_path = output_dir / f"{entry_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)
    log_message(f"Entry written: {out_path.name}")

def build_location_string(location: dict) -> str:
    description = (location.get("description") or "").strip()
    city        = (location.get("city") or "").strip()
    state       = (location.get("state") or "").strip()
    country     = (location.get("country") or "").strip()

    country_normalized = COUNTRY_NORMALIZE.get(country.lower(), country)

    parts = []
    if description.lower() not in JUNK_VALUES:
        parts.append(description)
    if city.lower() not in JUNK_VALUES:
        parts.append(city)
    if state.lower() not in JUNK_VALUES:
        parts.append(state)
    if country_normalized and country_normalized.upper() != "USA":
        parts.append(country_normalized)
    elif not parts and country_normalized:
        parts.append(country_normalized)

    return ", ".join(parts)

def build_embed_text(entry: dict) -> str:
    parts = []
    if entry.get("title"):
        parts.append(f"Title: {entry['title']}")
    if entry.get("category") and entry.get("type"):
        parts.append(f"Type: {entry['category']} / {entry['type']}")
    loc = entry.get("location", {})
    loc_str = build_location_string(loc)
    if loc_str:
        parts.append(f"Location: {loc_str}")
    if entry.get("date_of_event"):
        parts.append(f"Date: {entry['date_of_event']}")
    if entry.get("tags"):
        parts.append(f"Tags: {', '.join(entry['tags'])}")
    if entry.get("physical_evidence") is True:
        parts.append("Physical evidence: yes")
    if entry.get("summary"):
        summary = entry["summary"]
        if len(summary) > SUMMARY_THRESHOLD:
            log_message(f"Summary over {SUMMARY_THRESHOLD} chars — sending to OpenAI...")
            # summary = summarize_with_deepseek(summary, target_chars=800)
            summary = summarize_with_openai(summary, target_chars=800)
        parts.append(f"Summary: {summary}")
    return "\n".join(parts)

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS) / relative_path
    return Path(__file__).parent / relative_path

#--safe_str and safe_float are to fix potential NaNs from popping up and to deal with floats--#
def safe_str(val) -> str | None:
    """Convert a value to stripped string, return None if empty/NaN."""
    if val is None:
        return None
    try:
        import math
        if isinstance(val, float) and math.isnan(val):
            return None
    except Exception:
        pass
    s = str(val).strip()
    return s if s and s.lower() not in ("nan", "none", "") else None

def safe_float(val) -> float | None:
    """Convert to float, return None on failure."""
    try:
        return float(val)
    except (ValueError, TypeError):
        return None

def get_category():
    val = category_var.get()
    if val == "Other (enter below)":
        return other_category_var.get().strip() or "Unknown"
    elif val == "— select —":
        return "Unknown"
    return val

def refresh_category_dropdown():
    category_dropdown["values"] = get_categories()

def bulk_upload_json():
    file_paths = filedialog.askopenfilenames(
        title="Select JSON files to upload",
        filetypes=[("JSON files", "*.json")]
    )
    if not file_paths:
        log_message("No files selected")
        return

    # Load and validate all files first
    entries = []
    skipped = 0
    for fp in file_paths:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                entry = json.load(f)
            if not isinstance(entry, dict):
                log_message(f"Skipping {Path(fp).name} — not a valid entry dict")
                skipped += 1
                continue
            entries.append(entry)
        except Exception as e:
            log_message(f"Skipping {Path(fp).name} — {e}")
            skipped += 1

    if not entries:
        log_message("No valid JSON entries found in selected files")
        return

    if skipped:
        log_message(f"Skipped {skipped} invalid files")

    # Show confirmation dialog
    show_bulk_upload_ui(entries)

def show_bulk_upload_ui(entries: list):
    win = tk.Toplevel()
    win.iconbitmap(resource_path("all_seeing_eye.ico"))
    win.title("Bulk Upload")
    win.grab_set()
    win.resizable(False, False)

    # --- Cost estimation ---
    # Embedding cost: based on existing embed_text length
    embed_texts  = [e.get("embed_text") or "" for e in entries]
    embed_chars  = sum(len(t) for t in embed_texts)
    embed_tok    = embed_chars / CHARS_PER_TOKEN
    embed_cost   = embed_tok / 1_000_000 * EMBED_PRICE_PER_M

    # Re-summarisation cost (hypothetical): based on summary fields over threshold
    summaries    = [e.get("summary") or "" for e in entries]
    long_sums    = [s for s in summaries if len(s) > SUMMARY_THRESHOLD]
    resumm_in_tok  = sum(len(s) for s in long_sums) / CHARS_PER_TOKEN
    resumm_out_tok = len(long_sums) * SUMMARY_TARGET / CHARS_PER_TOKEN
    resumm_cost  = (resumm_in_tok / 1_000_000 * SUMM_INPUT_PER_M
                  + resumm_out_tok / 1_000_000 * SUMM_OUTPUT_PER_M)

    # --- Header ---
    ttk.Label(win, text="Bulk Upload to Compendium",
              font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=2, padx=16, pady=(16, 4), sticky="w")
    ttk.Separator(win, orient="horizontal").grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=4)

    # --- Stats ---
    ttk.Label(win, text="Files selected:",           foreground="gray", width=36).grid(row=2, column=0, padx=16, pady=2, sticky="w")
    ttk.Label(win, text=f"{len(entries):,}").grid(row=2, column=1, padx=8, pady=2, sticky="w")

    ttk.Label(win, text="Need re-summarisation (>1k):", foreground="gray", width=36).grid(row=3, column=0, padx=16, pady=2, sticky="w")
    ttk.Label(win, text=f"{len(long_sums):,}  ({round(len(long_sums)/len(entries)*100, 1) if entries else 0}%)").grid(row=3, column=1, padx=8, pady=2, sticky="w")

    ttk.Separator(win, orient="horizontal").grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=8)

    ttk.Label(win, text="Embedding cost (this run):",       foreground="gray", width=36).grid(row=5, column=0, padx=16, pady=2, sticky="w")
    ttk.Label(win, text=f"${embed_cost:.4f}",
              font=("Segoe UI", 9, "bold")).grid(row=5, column=1, padx=8, pady=2, sticky="w")

    ttk.Label(win, text="Re-summarisation cost (if opted):", foreground="gray", width=36).grid(row=6, column=0, padx=16, pady=2, sticky="w")
    ttk.Label(win, text=f"${resumm_cost:.4f}",
              foreground="gray").grid(row=6, column=1, padx=8, pady=2, sticky="w")

    ttk.Separator(win, orient="horizontal").grid(row=7, column=0, columnspan=2, sticky="ew", padx=16, pady=8)

    # --- Checkbox ---
    rebuild_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(win, text="Rebuild embed_text from scratch (re-summarises long entries)",
                    variable=rebuild_var).grid(row=8, column=0, columnspan=2, padx=16, pady=(0, 8), sticky="w")

    ttk.Separator(win, orient="horizontal").grid(row=9, column=0, columnspan=2, sticky="ew", padx=16, pady=4)

    # --- Footer ---
    footer = ttk.Frame(win)
    footer.grid(row=10, column=0, columnspan=2, padx=16, pady=(0, 16), sticky="e")
    ttk.Button(footer, text="Cancel", command=win.destroy).pack(side="right", padx=(8, 0))
    ttk.Button(footer, text="Upload", command=lambda: on_upload()).pack(side="right")

    def on_upload():
        global openai_client, dynamodb, s3_client, pinecone_index
        if not all([openai_client, dynamodb, s3_client, pinecone_index]):
            log_message("ERROR: One or more clients not initialized — check Settings")
            return

        rebuild = rebuild_var.get()
        win.destroy()

        thread = threading.Thread(
            target=run_bulk_upload,
            args=(entries, rebuild),
            daemon=True
        )
        thread.start()

def run_bulk_upload(entries: list, rebuild_embed: bool):
    success  = 0
    skipped  = 0
    failed   = 0

    log_message(f"Starting bulk upload — {len(entries)} entries")

    for i, entry in enumerate(entries, 1):
        try:
            if rebuild_embed:
                log_message(f"  Rebuilding embed_text for: {entry.get('title') or entry.get('id', '?')}")
                entry["embed_text"] = build_embed_text(entry)

            result = ingest_to_compendium(entry)
            if result == True:
                success += 1
            elif result == "duplicate":
                skipped += 1
            else:
                failed += 1

        except Exception as e:
            log_message(f"  ✗ Error on entry {i}: {e}")
            failed += 1

        if i % 10 == 0 or i == len(entries):
            log_message(f"  Progress: {i}/{len(entries)}")
            root.update_idletasks()

    log_message("─" * 40)
    log_message(f"Bulk upload complete — {len(entries)} entries")
    log_message(f"  Ingested: {success}")
    log_message(f"  Duplicates skipped: {skipped}")
    if failed:
        log_message(f"  Failed: {failed}")
    root.update_idletasks()

#--Search for files to import to Compendium--#
def browse_file():
    file_path = filedialog.askopenfilename(
        title="Select your file",
        filetypes=[("Excel Files", "*.xlsx"), ("CSV Files", "*.csv"), ("PDF Files", "*.pdf")]
    )

    if not file_path:
        log_message("No file selected; click 'Browse' to select a file")
        return
    
    ext = Path(file_path).suffix.lower()

    try:
        if ext == ".csv":
            df = pd.read_csv(file_path, low_memory=False)
            log_message("CSV file loaded successfully")
            mapping, unmatched = detect_column_mapping(df.columns)
            show_mapping_ui(df.columns, mapping, file_path, df, on_complete=lambda m, d: threading.Thread(
    target=map_rows, args=(df, m, file_path, get_category(), d), daemon=True
).start())
            
        elif ext == ".xlsx":
            df = pd.read_excel(file_path, low_memory=False)
            log_message("Excel file loaded successfully")
            mapping, unmatched = detect_column_mapping(df.columns)
            show_mapping_ui(df.columns, mapping, file_path, df, on_complete=lambda m, d: threading.Thread(
    target=map_rows, args=(df, m, file_path, get_category(), d), daemon=True
).start())

        # elif ext == ".docx":
        #     log_message("Word file loaded successfully")
        #     # --Placeholder for docx processing function--#

        elif ext == ".pdf":
            with pdfplumber.open(file_path) as pdf:
                raw = "\n".join(page.extract_text() or "" for page in pdf.pages)
            if not raw.strip():
                log_message("Warning: PDF appears to be image-based; no text extracted")
            else:
                log_message("PDF file loaded successfully")
            show_pdf_metadata_ui(raw, file_path, on_complete=lambda entry, d: threading.Thread(
    target=process_and_deliver, args=([entry], Path(file_path).stem, d), daemon=True
).start())

        else:
            log_message(f"Unsupported file type: {ext}")

    except FileNotFoundError:
        log_message("File not found; Please select a different file")
    except PermissionError:
        log_message("Permission denied; cannot open that file")
    except Exception as e:
        log_message(f"Error loading file: {e}")

def start_processing(df, final_mapping, file_path, category):
    thread = threading.Thread(
        target=map_rows,
        args=(df, final_mapping, file_path, category),
        daemon=True
    )
    thread.start()

def process_and_deliver(entries: list, source_stem: str, destinations: dict):
    """
    One-pass delivery: for each entry, write local JSON and/or ingest to AWS
    based on the destinations dict — no double processing.
    """
    want_local      = destinations.get("local_export", False)
    want_compendium = destinations.get("compendium", False)

    run_dir      = None
    success_aws  = 0
    success_local = 0
    failed       = 0

    if want_local:
        try:
            run_dir = get_run_output_dir(source_stem)
            log_message(f"Local export folder: {run_dir}")
        except ValueError as e:
            log_message(f"Local export error: {e}")
            want_local = False

    log_message(f"Starting delivery — {len(entries)} entries")

    for i, entry in enumerate(entries, 1):
        entry_ok = True

        if want_local and run_dir:
            try:
                write_entry_local(entry, run_dir)
                success_local += 1
            except Exception as e:
                log_message(f"  ✗ Local write error: {e}")
                entry_ok = False

        if want_compendium:
            result = ingest_to_compendium(entry)
            if result == True:
                success_aws += 1
            elif result == "duplicate":
                pass  # don't count as success or failure
            else:
                entry_ok = False

        if not entry_ok:
            failed += 1

        if i % 10 == 0 or i == len(entries):
            log_message(f"  Progress: {i}/{len(entries)}")
            root.update_idletasks()

    # Run summary
    summary = {
        "source":          source_stem,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "total_entries":   len(entries),
        "local_written":   success_local if want_local else "n/a",
        "compendium_ingested": success_aws if want_compendium else "n/a",
        "duplicates_skipped":  (len(entries) - success_aws - failed) if want_compendium else "n/a",
        "failed":          failed,
    }

    if want_local and run_dir:
        write_run_summary(run_dir, summary)

    log_message("─" * 40)
    log_message(f"Run complete — {len(entries)} entries")
    if want_local:
        log_message(f"  Local: {success_local} written → {run_dir}")
    if want_compendium:
        skipped_count = len(entries) - success_aws - failed
        log_message(f"  Compendium: {success_aws} ingested, {skipped_count} duplicates skipped")
    if failed:
        log_message(f"  Failed: {failed}")
    root.update_idletasks()

def entry_exists(source: str, entry_id: str) -> bool:
    try:
        from boto3.dynamodb.conditions import Key
        table    = dynamodb.Table(DYNAMO_TABLE)
        response = table.query(
            IndexName="source-id-index",
            KeyConditionExpression=Key("source").eq(source) & Key("entry_id").eq(entry_id)
        )
        return response["Count"] > 0
    except Exception as e:
        log_message(f"  → Duplicate check error: {e}")
        return False
    
def ingest_to_compendium(entry: dict) -> bool:
    """Write one entry to S3, DynamoDB, and Pinecone. Returns True on success."""
    try:
        entry_id  = entry.get("id") or str(uuid.uuid4())
        raw_text  = entry.get("raw_text") or entry.get("summary") or ""
        embed_str = entry.get("embed_text") or ""
        source        = entry.get("source") or ""

        # Duplicate check
        if entry_exists(source, entry_id):
            log_message(f"  → Skipping duplicate: {entry.get('title') or entry_id}")
            return "duplicate"
        
        # S3 — raw text
        s3_key = f"entries/{entry_id}.txt"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=raw_text.encode("utf-8")
        )

        # DynamoDB — metadata
        table = dynamodb.Table(DYNAMO_TABLE)
        table.put_item(Item={
            "entry_id":        entry_id,
            "title":           entry.get("title") or "Untitled",
            "category":        entry.get("category") or "Unknown",
            "type":            entry.get("type") or "account",
            "source":          entry.get("source") or "",
            "summary":         entry.get("summary") or "",
            "tags":            entry.get("tags") or [],
            "date_of_event":   entry.get("date_of_event") or "unknown",
            "date_added":      datetime.now(timezone.utc).isoformat(),
            "location":        entry.get("location") or {},
            "witness_count":   entry.get("witness_count"),
            "physical_evidence": entry.get("physical_evidence"),
            "notes":           entry.get("notes"),
            "s3_key":          s3_key,
        })

        # Pinecone — vector
        vector = embed_text_with_openai(embed_str)
        pinecone_index.upsert(vectors=[{
            "id":     entry_id,
            "values": vector,
            "metadata": {
                "title":    entry.get("title") or "",
                "category": entry.get("category") or "",
                "tags":     entry.get("tags") or [],
            }
        }])

        return True

    except Exception as e:
        log_message(f"  ✗ Ingest error for '{entry.get('title', '?')}': {e}")
        return False

def get_run_output_dir(source_stem: str) -> Path:
    """Return a timestamped subfolder inside the configured output directory."""
    config     = load_config()
    output_dir = config.get("output_dir", "")
    if not output_dir:
        raise ValueError("No output directory configured — check Settings")
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir    = Path(output_dir) / f"{source_stem}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir

def write_entry_local(entry: dict, run_dir: Path):
    entry_id = entry.get("id") or str(uuid.uuid4())
    out_path = run_dir / f"{entry_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)

def write_run_summary(run_dir: Path, summary: dict):
    out_path = run_dir / "_run_summary.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

#--Building the main window for the application--#
def log_message(message):
    log_text.config(state=tk.NORMAL)
    log_text.insert(tk.END, message + "\n")
    log_text.config(state=tk.DISABLED)
    log_text.yview(tk.END)

root = tk.Tk()
root.rowconfigure(0, weight=1)
root.columnconfigure(0, weight=1)
root.title("The Archivist")
root.iconbitmap(resource_path("all_seeing_eye.ico"))
menubar = tk.Menu(root)
root.config(menu=menubar)

file_menu = tk.Menu(menubar, tearoff=0)
menubar.add_cascade(label="File", menu=file_menu)
file_menu.add_command(label="Browse file", command=browse_file)
file_menu.add_command(label="Bulk upload JSON", command=bulk_upload_json)
file_menu.add_separator()
file_menu.add_command(label="Exit", command=root.quit)

settings_menu = tk.Menu(menubar, tearoff=0)
menubar.add_cascade(label="Settings", menu=settings_menu)
settings_menu.add_command(label="Preferences", command=show_settings)

style = ttk.Style()
style.theme_use("xpnative")  # or "vista" — both pull Windows native controls
frame = ttk.Frame(root)
frame.pack(pady=5)

# username_label = ttk.Label(frame, text="Username:")
# username_label.pack()
# username_entry = ttk.Entry(frame, width=30)
# username_entry.pack(pady=2)

# password_label = ttk.Label(frame, text="Password")
# password_label.pack()
# password_entry = ttk.Entry(frame, width=30, show="*")
# password_entry.pack(pady=2)

# button_example = ttk.Button(frame, text="Browse", command=browse_file, state=tk.NORMAL)
# button_example.pack(side=tk.LEFT, padx=5)

category_label = ttk.Label(frame, text="Category:")
category_label.pack()
category_var = tk.StringVar(value="-Select-")
category_dropdown = ttk.Combobox(frame, textvariable=category_var, 
    values=get_categories(),
    state="readonly", width=27)
category_dropdown.pack(pady=2)
other_category_var = tk.StringVar()

def on_category_change(event):
    if category_var.get() == "Other (Enter Below)":
        other_entry.pack(pady=(0, 2))
    else:
        other_entry.pack_forget()

other_entry = ttk.Entry(frame, textvariable=other_category_var, width=27)

category_dropdown.bind("<<ComboboxSelected>>", on_category_change)

log_text = scrolledtext.ScrolledText(root, state=tk.DISABLED, width=93, height=25)
log_text.pack(pady=10, fill="both", expand=True)
log_text.tag_configure("italic", font=("Courier", 10, "italic"))

log_text.config(state=tk.NORMAL)
log_text.insert(tk.END, '"There is nothing so powerful as truth, and often nothing so strange." — Daniel Webster\n\n', "italic")
# log_text.insert(tk.END, "— Daniel Webster\n\n", "italic")
log_text.config(state=tk.DISABLED)

# log_message('"There is nothing so powerful as truth,—and often nothing so strange."\n—Daniel Webster\n\nWelcome to the Archivist. Click File to choose an upload path, or click Settings to\nchange your preferences\n ')
log_message('Welcome to The Archivist. Click File to choose an upload path, or click Settings to change\nyour preferences\n ')

# try:
#     requests.get("http://localhost:11434/api/tags", timeout=2)
#     log_message("Ollama running — DeepSeek ready")
# except:
#     log_message("WARNING: Ollama not running — DeepSeek will fall back to truncation")

init_clients()
root.mainloop()