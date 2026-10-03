"""
dashboard_ui.py - Ultra-Modern Desktop Dashboard for WinVoice.
Aesthetic: Raycast / Wispr Flow / Linear dark minimalist UI.
Features:
- Sleek topbar with active engine status & quick metric chips
- Pill-based tab navigation (History, Models, Settings)
- Rich Dictation History cards with animated 'Copied! ✓' feedback
- Interactive Model Selection cards with spec pills & instant switching
- Preferences toggles for AI Enhancer, Windows Autostart & Audio Chimes
"""

import os
import time
from datetime import datetime
import tkinter as tk
import customtkinter as ctk
import pyperclip
from typing import Callable, Optional, List, Dict

import history_manager
import win_startup
import settings_manager

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# Theme Palette (Deep Obsidian & Indigo/Violet Accents)
C_BG = "#0d0e13"            # Main window background
C_HEADER = "#13141c"        # Header background
C_CARD_BG = "#161722"       # Card surface
C_CARD_BORDER = "#252738"   # Subtle card border
C_CARD_ACTIVE = "#1e2030"   # Highlighted card surface
C_ACCENT = "#6366f1"        # Primary accent (indigo)
C_ACCENT_HOVER = "#4f46e5"  # Accent hover
C_EMERALD = "#10b981"       # Success green
C_EMERALD_BG = "#064e3b"    # Green chip bg
C_TEXT_MAIN = "#f8fafc"     # Primary text
C_TEXT_MUTED = "#94a3b8"    # Secondary text
C_TEXT_FAINT = "#64748b"    # Faint text
C_HOVER_BG = "#222436"      # Generic hover bg

MODEL_CARDS_DATA = {
    "base": {
        "title": "Whisper Base",
        "badge": "⚡ Ultra Fast · CPU",
        "tag_color": "#3b82f6",
        "specs": ["75 MB", "CPU Engine", "~350ms Latency", "0 MB VRAM"],
        "desc": "Ultra-low latency instant transcription. Best for fast English dictation, daily typing, messaging, and coding prompts with minimal CPU footprint.",
        "highlight": "Recommended for English"
    },
    "hinglish_swift": {
        "title": "Hinglish Swift",
        "badge": "🇮🇳 Bilingual · CUDA",
        "tag_color": "#f59e0b",
        "specs": ["145 MB", "CUDA Engine", "~250ms Latency", "Romanized"],
        "desc": "Fine-tuned acoustic model specializing in colloquial Indian English & Romanized Hindi speech ('kya haal hai bro', 'ek baar check karo').",
        "highlight": "Best for Hindi + English"
    },
    "small": {
        "title": "Whisper Small",
        "badge": "🎯 Studio Precision",
        "tag_color": "#10b981",
        "specs": ["240 MB", "High Precision", "~800ms Latency", "CPU/CUDA"],
        "desc": "Full acoustic recognition with expanded vocabulary. Excels at complex technical terms, programming jargon, and background noise.",
        "highlight": "Maximum Accuracy"
    }
}


def format_timestamp(ts_str: str) -> str:
    """Format raw timestamp into user-friendly string."""
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        if dt.date() == now.date():
            return f"Today at {dt.strftime('%I:%M %p')}"
        elif (now.date() - dt.date()).days == 1:
            return f"Yesterday at {dt.strftime('%I:%M %p')}"
        else:
            return dt.strftime("%b %d, %I:%M %p")
    except Exception:
        return ts_str


class DashboardUI:
    def __init__(self, on_model_changed: Optional[Callable] = None):
        self.root = None
        self.on_model_changed = on_model_changed
        self.settings = settings_manager.load_settings()

        # Navigation state
        self.current_tab = "history"  # "history", "models", "settings"
        self.nav_buttons = {}

        # Content frames
        self.content_container = None
        self.history_frame = None
        self.models_container = None
        self.settings_container = None

        # Widgets
        self.status_badge = None
        self.stat_count_lbl = None
        self.stat_latency_lbl = None
        self.stat_enhancer_lbl = None
        self.model_card_widgets = {}

    def init_ui(self):
        """Build the dashboard window."""
        if self.root:
            return

        self.root = ctk.CTk()
        self.root.title("Quotal")
        self.root.geometry("720x660")
        self.root.minsize(640, 560)
        self.root.configure(fg_color=C_BG)

        # Set Window Icon
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.ico")
        if os.path.exists(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except Exception:
                pass

        # Handle window close: Hide instead of exit (stays in system tray)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)

        # 1. Top Header Bar
        self._build_header()

        # 2. Live Stats Ribbon
        self._build_stats_ribbon()

        # 3. Modern Segmented Tab Navigation
        self._build_navigation_bar()

        # 4. Main Dynamic Content Container
        self.content_container = ctk.CTkFrame(self.root, fg_color="transparent")
        self.content_container.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        # Build the 3 views inside content_container
        self._build_history_view()
        self._build_models_view()
        self._build_settings_view()

        # Show initial tab
        self._switch_tab("history")

    def _build_header(self):
        """Header with glowing branding and active engine status badge."""
        header = ctk.CTkFrame(self.root, corner_radius=0, fg_color=C_HEADER, height=68)
        header.pack(fill="x", padx=0, pady=0)
        header.pack_propagate(False)

        # Left branding
        brand_left = ctk.CTkFrame(header, fg_color="transparent")
        brand_left.pack(side="left", padx=20, pady=12)

        # Icon squircle with official Quotal Logo
        icon_box = ctk.CTkFrame(
            brand_left,
            width=40,
            height=40,
            corner_radius=10,
            fg_color="#181924",
            border_width=1,
            border_color="#2b2d42"
        )
        icon_box.pack(side="left", padx=(0, 12))
        icon_box.pack_propagate(False)

        icon_png = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quotal_icon.png")
        if os.path.exists(icon_png):
            try:
                from PIL import Image
                pil_icon = Image.open(icon_png).convert("RGBA")
                self.logo_img = ctk.CTkImage(light_image=pil_icon, dark_image=pil_icon, size=(34, 34))
                ctk.CTkLabel(icon_box, image=self.logo_img, text="").place(relx=0.5, rely=0.5, anchor="center")
            except Exception:
                ctk.CTkLabel(icon_box, text="❝", font=ctk.CTkFont(size=20, weight="bold"), text_color="#ffffff").place(relx=0.5, rely=0.5, anchor="center")
        else:
            ctk.CTkLabel(icon_box, text="❝", font=ctk.CTkFont(size=20, weight="bold"), text_color="#ffffff").place(relx=0.5, rely=0.5, anchor="center")

        # Titles
        titles_box = ctk.CTkFrame(brand_left, fg_color="transparent")
        titles_box.pack(side="left")

        ctk.CTkLabel(
            titles_box,
            text="Quotal",
            font=ctk.CTkFont(family="Segoe UI", size=19, weight="bold"),
            text_color=C_TEXT_MAIN
        ).pack(anchor="w")

        ctk.CTkLabel(
            titles_box,
            text="Voice Dictation · Wispr Flow for Windows",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=C_TEXT_MUTED
        ).pack(anchor="w")

        # Right Active Engine Badge
        right_box = ctk.CTkFrame(header, fg_color="transparent")
        right_box.pack(side="right", padx=20, pady=16)

        current_key = self.settings.get("model_key", "base")
        current_cfg = settings_manager.MODEL_CONFIGS.get(current_key, {})
        model_name = current_cfg.get("name", "Whisper Base").split("(")[0].strip()

        self.status_badge = ctk.CTkLabel(
            right_box,
            text=f"● {model_name} · Active",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=C_EMERALD,
            fg_color=C_EMERALD_BG,
            corner_radius=20,
            padx=14,
            pady=5
        )
        self.status_badge.pack(side="right")

    def _build_stats_ribbon(self):
        """Quick metric chips bar."""
        ribbon = ctk.CTkFrame(self.root, fg_color="transparent")
        ribbon.pack(fill="x", padx=20, pady=(12, 10))

        # 4 Metric Cards
        self.stat_count_lbl = self._create_stat_chip(
            ribbon,
            icon="📊",
            title="Total Dictations",
            initial_value="0"
        )
        self.stat_latency_lbl = self._create_stat_chip(
            ribbon,
            icon="⚡",
            title="Avg Latency",
            initial_value="~350ms"
        )
        self.stat_enhancer_lbl = self._create_stat_chip(
            ribbon,
            icon="✨",
            title="AI Polishing",
            initial_value="Active" if self.settings.get("ai_enhance", True) else "Off"
        )
        self._create_stat_chip(
            ribbon,
            icon="⌨️",
            title="Hardware Trigger",
            initial_value="[Right Alt] / [F8]"
        )

    def _create_stat_chip(self, parent, icon: str, title: str, initial_value: str) -> ctk.CTkLabel:
        chip = ctk.CTkFrame(
            parent,
            fg_color=C_CARD_BG,
            corner_radius=10,
            border_width=1,
            border_color=C_CARD_BORDER
        )
        chip.pack(side="left", fill="both", expand=True, padx=4)

        inner = ctk.CTkFrame(chip, fg_color="transparent")
        inner.pack(padx=12, pady=8, fill="x")

        top_row = ctk.CTkFrame(inner, fg_color="transparent")
        top_row.pack(anchor="w")

        ctk.CTkLabel(
            top_row,
            text=f"{icon} {title}",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=C_TEXT_MUTED
        ).pack(side="left")

        val_lbl = ctk.CTkLabel(
            inner,
            text=initial_value,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=C_TEXT_MAIN
        )
        val_lbl.pack(anchor="w", pady=(2, 0))
        return val_lbl

    def _build_navigation_bar(self):
        """Segmented pill navigation bar."""
        nav_wrapper = ctk.CTkFrame(self.root, fg_color="transparent")
        nav_wrapper.pack(fill="x", padx=20, pady=(4, 12))

        nav_bar = ctk.CTkFrame(
            nav_wrapper,
            fg_color=C_CARD_BG,
            corner_radius=12,
            border_width=1,
            border_color=C_CARD_BORDER,
            height=46
        )
        nav_bar.pack(fill="x")
        nav_bar.pack_propagate(False)

        tabs = [
            ("history", "📜 Dictation History"),
            ("models", "⚡ Speech Engines"),
            ("settings", "⚙️ Settings & Shortcuts")
        ]

        for tab_id, tab_label in tabs:
            btn = ctk.CTkButton(
                nav_bar,
                text=tab_label,
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                corner_radius=9,
                height=34,
                fg_color="transparent",
                text_color=C_TEXT_MUTED,
                hover_color=C_HOVER_BG,
                command=lambda t=tab_id: self._switch_tab(t)
            )
            btn.pack(side="left", fill="both", expand=True, padx=6, pady=6)
            self.nav_buttons[tab_id] = btn

    def _switch_tab(self, tab_id: str):
        """Smoothly toggle between content views."""
        self.current_tab = tab_id

        # Update button visual states
        for tid, btn in self.nav_buttons.items():
            if tid == tab_id:
                btn.configure(
                    fg_color=C_ACCENT,
                    text_color="#ffffff",
                    hover_color=C_ACCENT_HOVER
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=C_TEXT_MUTED,
                    hover_color=C_HOVER_BG
                )

        # Toggle view containers
        if self.history_container:
            self.history_container.pack_forget()
        if self.models_container:
            self.models_container.pack_forget()
        if self.settings_container:
            self.settings_container.pack_forget()

        if tab_id == "history":
            self.history_container.pack(fill="both", expand=True)
            self.refresh_history()
        elif tab_id == "models":
            self.models_container.pack(fill="both", expand=True)
            self._refresh_model_cards()
        elif tab_id == "settings":
            self.settings_container.pack(fill="both", expand=True)

    # -------------------------------------------------------------------------
    # TAB 1: HISTORY VIEW
    # -------------------------------------------------------------------------
    def _build_history_view(self):
        """Construct the Dictation History tab."""
        self.history_container = ctk.CTkFrame(self.content_container, fg_color="transparent")

        # Action Bar above scroll list
        action_bar = ctk.CTkFrame(self.history_container, fg_color="transparent")
        action_bar.pack(fill="x", padx=4, pady=(2, 8))

        self.history_title_lbl = ctk.CTkLabel(
            action_bar,
            text="Recent Dictations",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=C_TEXT_MAIN
        )
        self.history_title_lbl.pack(side="left")

        # Buttons on right
        ctk.CTkButton(
            action_bar,
            text="🔄 Refresh",
            width=86,
            height=30,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#1e202f",
            hover_color=C_HOVER_BG,
            text_color=C_TEXT_MAIN,
            corner_radius=8,
            command=self.refresh_history
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            action_bar,
            text="🗑️ Clear All",
            width=86,
            height=30,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#2b1418",
            hover_color="#3d1b20",
            text_color="#f87171",
            corner_radius=8,
            command=self._clear_history
        ).pack(side="right")

        # Scrollable container for history items
        self.history_frame = ctk.CTkScrollableFrame(
            self.history_container,
            fg_color="transparent",
            scrollbar_button_color="#2b2d3d",
            scrollbar_button_hover_color="#454860"
        )
        self.history_frame.pack(fill="both", expand=True, padx=0, pady=0)

    def refresh_history(self):
        """Populate history cards with modern styling."""
        if not self.history_frame:
            return

        for widget in self.history_frame.winfo_children():
            widget.destroy()

        entries = history_manager.get_recent(limit=50)

        # Update stats ribbon
        total_dictations = len(entries)
        if self.stat_count_lbl:
            self.stat_count_lbl.configure(text=str(total_dictations))

        if entries and self.stat_latency_lbl:
            recent_latencies = [e.get("latency_ms", 0) for e in entries[:10] if e.get("latency_ms", 0) > 0]
            if recent_latencies:
                avg_lat = sum(recent_latencies) / len(recent_latencies)
                self.stat_latency_lbl.configure(text=f"~{avg_lat:.0f}ms")

        if self.history_title_lbl:
            self.history_title_lbl.configure(text=f"Recent Dictations ({total_dictations})")

        if not entries:
            empty_box = ctk.CTkFrame(
                self.history_frame,
                fg_color=C_CARD_BG,
                corner_radius=12,
                border_width=1,
                border_color=C_CARD_BORDER
            )
            empty_box.pack(fill="x", padx=10, pady=50)

            ctk.CTkLabel(
                empty_box,
                text="🎙️ No dictations recorded yet",
                font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
                text_color=C_TEXT_MAIN
            ).pack(pady=(28, 6))

            ctk.CTkLabel(
                empty_box,
                text="Hold [Right Alt] or [F8] anywhere to speak.\nWhen you release the key, text will appear here and paste at your cursor!",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color=C_TEXT_MUTED,
                justify="center"
            ).pack(pady=(0, 28))
            return

        for item in entries:
            self._render_history_card(item)

    def _render_history_card(self, item: Dict):
        """Render a single modern dictation history card."""
        card = ctk.CTkFrame(
            self.history_frame,
            fg_color=C_CARD_BG,
            corner_radius=12,
            border_width=1,
            border_color=C_CARD_BORDER
        )
        card.pack(fill="x", padx=4, pady=6)

        # Top Meta Row
        meta_row = ctk.CTkFrame(card, fg_color="transparent")
        meta_row.pack(fill="x", padx=14, pady=(10, 6))

        # Time
        raw_ts = item.get("timestamp", "")
        formatted_time = format_timestamp(raw_ts)

        ctk.CTkLabel(
            meta_row,
            text=f"🕒 {formatted_time}",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=C_TEXT_MUTED
        ).pack(side="left")

        # Duration & Latency Chip
        dur = item.get("duration_sec", 0)
        lat = item.get("latency_ms", 0)
        dur_pill = ctk.CTkFrame(meta_row, fg_color="#1e2030", corner_radius=6)
        dur_pill.pack(side="left", padx=10)
        ctk.CTkLabel(
            dur_pill,
            text=f"⏱️ {dur}s spoke · ⚡ {lat:.0f}ms",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color="#818cf8",
            padx=8,
            pady=2
        ).pack()

        # Word count
        words = item.get("word_count", len(item.get("cleaned", "").split()))
        ctk.CTkLabel(
            meta_row,
            text=f"{words} words",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=C_TEXT_FAINT
        ).pack(side="left")

        # Animated Copy Button
        text_to_copy = item.get("cleaned", "")
        copy_btn = ctk.CTkButton(
            meta_row,
            text="📋 Copy",
            width=76,
            height=26,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#202234",
            hover_color=C_HOVER_BG,
            text_color=C_TEXT_MAIN,
            corner_radius=7
        )
        copy_btn.configure(command=lambda btn=copy_btn, t=text_to_copy: self._on_copy_clicked(btn, t))
        copy_btn.pack(side="right")

        # Text Content Box
        text_box = ctk.CTkFrame(card, fg_color="transparent")
        text_box.pack(fill="x", padx=14, pady=(0, 12))

        content_lbl = ctk.CTkLabel(
            text_box,
            text=text_to_copy,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=C_TEXT_MAIN,
            wraplength=590,
            justify="left",
            anchor="w"
        )
        content_lbl.pack(fill="x", anchor="w")

    def _on_copy_clicked(self, btn: ctk.CTkButton, text: str):
        """Copies text to clipboard with animated feedback."""
        pyperclip.copy(text)
        btn.configure(
            text="✓ Copied!",
            fg_color="#059669",
            hover_color="#047857",
            text_color="#ffffff"
        )
        # Revert back after 1.4 seconds
        self.root.after(1400, lambda: self._revert_copy_btn(btn))

    def _revert_copy_btn(self, btn: ctk.CTkButton):
        try:
            btn.configure(
                text="📋 Copy",
                fg_color="#202234",
                hover_color=C_HOVER_BG,
                text_color=C_TEXT_MAIN
            )
        except Exception:
            pass

    def _clear_history(self):
        history_manager.clear_history()
        self.refresh_history()

    # -------------------------------------------------------------------------
    # TAB 2: SPEECH ENGINES & MODELS VIEW
    # -------------------------------------------------------------------------
    def _build_models_view(self):
        """Construct the Speech Models Selection tab."""
        self.models_container = ctk.CTkScrollableFrame(
            self.content_container,
            fg_color="transparent",
            scrollbar_button_color="#2b2d3d",
            scrollbar_button_hover_color="#454860"
        )

        # Intro banner
        intro_card = ctk.CTkFrame(
            self.models_container,
            fg_color=C_CARD_BG,
            corner_radius=12,
            border_width=1,
            border_color=C_CARD_BORDER
        )
        intro_card.pack(fill="x", padx=4, pady=(2, 12))

        ctk.CTkLabel(
            intro_card,
            text="⚡ Local Speech Engines",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=C_TEXT_MAIN
        ).pack(anchor="w", padx=16, pady=(12, 2))

        ctk.CTkLabel(
            intro_card,
            text="Select your active model. All recognition runs 100% locally on your device with zero cloud latency or data sharing.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=C_TEXT_MUTED
        ).pack(anchor="w", padx=16, pady=(0, 12))

        # Model Cards
        current_key = self.settings.get("model_key", "base")
        for key, data in MODEL_CARDS_DATA.items():
            self._render_model_card(key, data, is_active=(key == current_key))

    def _render_model_card(self, key: str, data: Dict, is_active: bool):
        """Render an individual interactive model selection card."""
        card_border_color = C_ACCENT if is_active else C_CARD_BORDER
        card_bg_color = C_CARD_ACTIVE if is_active else C_CARD_BG

        card = ctk.CTkFrame(
            self.models_container,
            fg_color=card_bg_color,
            corner_radius=12,
            border_width=2 if is_active else 1,
            border_color=card_border_color
        )
        card.pack(fill="x", padx=4, pady=6)

        # Header of Card
        head_row = ctk.CTkFrame(card, fg_color="transparent")
        head_row.pack(fill="x", padx=16, pady=(14, 6))

        # Title & Category Tag
        title_box = ctk.CTkFrame(head_row, fg_color="transparent")
        title_box.pack(side="left")

        ctk.CTkLabel(
            title_box,
            text=data["title"],
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=C_TEXT_MAIN
        ).pack(side="left")

        tag_pill = ctk.CTkFrame(title_box, fg_color="#1e2030", corner_radius=6)
        tag_pill.pack(side="left", padx=10)
        ctk.CTkLabel(
            tag_pill,
            text=data["badge"],
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=data["tag_color"],
            padx=8,
            pady=2
        ).pack()

        # Action / Selection Button
        btn_text = "✓ Active Engine" if is_active else "Select Model"
        btn_fg = C_EMERALD_BG if is_active else "#222438"
        btn_txt_color = C_EMERALD if is_active else C_TEXT_MAIN
        btn_hover = C_EMERALD_BG if is_active else C_ACCENT

        select_btn = ctk.CTkButton(
            head_row,
            text=btn_text,
            width=120,
            height=30,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color=btn_fg,
            text_color=btn_txt_color,
            hover_color=btn_hover,
            corner_radius=8,
            command=lambda k=key: self._select_model(k)
        )
        select_btn.pack(side="right")

        # Spec Chips Row
        specs_row = ctk.CTkFrame(card, fg_color="transparent")
        specs_row.pack(fill="x", padx=16, pady=(0, 8))

        for spec in data["specs"]:
            spec_lbl = ctk.CTkLabel(
                specs_row,
                text=spec,
                font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
                text_color=C_TEXT_MUTED,
                fg_color="#1c1d29",
                corner_radius=5,
                padx=8,
                pady=2
            )
            spec_lbl.pack(side="left", padx=(0, 6))

        # Description
        desc_lbl = ctk.CTkLabel(
            card,
            text=data["desc"],
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=C_TEXT_MUTED,
            wraplength=580,
            justify="left"
        )
        desc_lbl.pack(anchor="w", padx=16, pady=(0, 14))

        self.model_card_widgets[key] = {
            "card": card,
            "btn": select_btn
        }

    def _select_model(self, selected_key: str):
        """User clicked to select a new model."""
        self.settings["model_key"] = selected_key
        settings_manager.save_settings(self.settings)

        # Update Top Header Status Badge
        model_name = MODEL_CARDS_DATA.get(selected_key, {}).get("title", "Whisper Base")
        if self.status_badge:
            self.status_badge.configure(text=f"● {model_name} · Active")

        # Refresh Card visual styles
        self._refresh_model_cards()

        # Trigger model swap in background dictation engine
        if self.on_model_changed:
            self.on_model_changed(selected_key)

    def _refresh_model_cards(self):
        """Update active borders and buttons across model cards."""
        current_key = self.settings.get("model_key", "base")
        for key, widgets in self.model_card_widgets.items():
            is_active = (key == current_key)
            card = widgets["card"]
            btn = widgets["btn"]

            card.configure(
                fg_color=C_CARD_ACTIVE if is_active else C_CARD_BG,
                border_color=C_ACCENT if is_active else C_CARD_BORDER,
                border_width=2 if is_active else 1
            )
            if is_active:
                btn.configure(
                    text="✓ Active Engine",
                    fg_color=C_EMERALD_BG,
                    text_color=C_EMERALD,
                    hover_color=C_EMERALD_BG
                )
            else:
                btn.configure(
                    text="Select Model",
                    fg_color="#222438",
                    text_color=C_TEXT_MAIN,
                    hover_color=C_ACCENT
                )

    # -------------------------------------------------------------------------
    # TAB 3: SETTINGS & SHORTCUTS VIEW
    # -------------------------------------------------------------------------
    def _build_settings_view(self):
        """Construct Settings and Shortcuts view."""
        self.settings_container = ctk.CTkScrollableFrame(
            self.content_container,
            fg_color="transparent",
            scrollbar_button_color="#2b2d3d",
            scrollbar_button_hover_color="#454860"
        )

        # 1. AI Enhancer Card
        self._build_setting_card(
            parent=self.models_container if False else self.settings_container,
            icon="✨",
            title="Smart AI Text Polisher",
            subtitle="Cleans speech hesitations ('um', 'uh', 'ah'), fixes misheard words ('badi' ➔ 'buddy'), removes stutters, and formats capitalization.",
            switch_initial=self.settings.get("ai_enhance", True),
            switch_command=self._toggle_enhancer
        )

        # 2. Windows Startup Card
        self._build_setting_card(
            parent=self.settings_container,
            icon="🚀",
            title="Launch on Windows Startup",
            subtitle="Automatically runs Quotal in the system tray when your computer boots so dictation is always instantly available.",
            switch_initial=win_startup.is_autostart_enabled(),
            switch_command=self._toggle_startup
        )

        # 3. Audio Chimes Card
        self._build_setting_card(
            parent=self.settings_container,
            icon="🔔",
            title="Audio Feedback Chimes",
            subtitle="Plays subtle, gentle audio tones when dictation starts and when text finishes pasting.",
            switch_initial=self.settings.get("audio_chimes", True),
            switch_command=self._toggle_chimes
        )

        # 4. Hardware Shortcuts & Usage Guide Card
        self._build_shortcuts_guide_card()

    def _build_setting_card(self, parent, icon: str, title: str, subtitle: str, switch_initial: bool, switch_command: Callable):
        card = ctk.CTkFrame(
            parent,
            fg_color=C_CARD_BG,
            corner_radius=12,
            border_width=1,
            border_color=C_CARD_BORDER
        )
        card.pack(fill="x", padx=4, pady=6)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=14)

        # Left info
        left_box = ctk.CTkFrame(inner, fg_color="transparent")
        left_box.pack(side="left", fill="both", expand=True)

        head_row = ctk.CTkFrame(left_box, fg_color="transparent")
        head_row.pack(anchor="w")

        ctk.CTkLabel(
            head_row,
            text=f"{icon}  {title}",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=C_TEXT_MAIN
        ).pack(side="left")

        ctk.CTkLabel(
            left_box,
            text=subtitle,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=C_TEXT_MUTED,
            wraplength=480,
            justify="left"
        ).pack(anchor="w", pady=(3, 0))

        # Right toggle switch
        switch_var = tk.BooleanVar(value=switch_initial)
        switch = ctk.CTkSwitch(
            inner,
            text="",
            variable=switch_var,
            progress_color=C_ACCENT,
            button_color="#ffffff",
            button_hover_color="#e0e7ff",
            command=lambda: switch_command(switch_var.get())
        )
        switch.pack(side="right", padx=(10, 0))

    def _build_shortcuts_guide_card(self):
        card = ctk.CTkFrame(
            self.settings_container,
            fg_color=C_CARD_BG,
            corner_radius=12,
            border_width=1,
            border_color=C_CARD_BORDER
        )
        card.pack(fill="x", padx=4, pady=6)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=16)

        ctk.CTkLabel(
            inner,
            text="⌨️  Hardware Trigger & Global Shortcuts",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=C_TEXT_MAIN
        ).pack(anchor="w")

        ctk.CTkLabel(
            inner,
            text="Quotal operates seamlessly over any active window or text input field.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=C_TEXT_MUTED
        ).pack(anchor="w", pady=(2, 12))

        # Keycap row
        keys_row = ctk.CTkFrame(inner, fg_color="transparent")
        keys_row.pack(fill="x", pady=(0, 10))

        self._create_keycap_pill(keys_row, "Right Alt", "Primary Trigger · Hold to Speak")
        self._create_keycap_pill(keys_row, "F8", "Alternative Key · Works on all keyboards")

        # How it works steps
        steps_box = ctk.CTkFrame(inner, fg_color="#13141d", corner_radius=8)
        steps_box.pack(fill="x", pady=(4, 0))

        steps = [
            ("1", "Place your cursor in any application (browser, VS Code, Discord, Word)."),
            ("2", "Hold [Right Alt] (or [F8]) — the fluid audio orb capsule appears."),
            ("3", "Speak naturally. When you finish speaking, simply release the key."),
            ("4", "Quotal cleans your speech and pastes the text directly at your cursor!")
        ]

        for num, text in steps:
            row = ctk.CTkFrame(steps_box, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=5)

            num_badge = ctk.CTkLabel(
                row,
                text=num,
                font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
                text_color=C_ACCENT,
                fg_color="#1e1b4b",
                corner_radius=6,
                width=20,
                height=20
            )
            num_badge.pack(side="left", padx=(0, 10))

            ctk.CTkLabel(
                row,
                text=text,
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color=C_TEXT_MAIN
            ).pack(side="left")

    def _create_keycap_pill(self, parent, keyname: str, desc: str):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=3)

        badge = ctk.CTkLabel(
            row,
            text=f"  {keyname}  ",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color="#ffffff",
            fg_color="#272738",
            corner_radius=6
        )
        badge.pack(side="left", padx=(0, 10))

        ctk.CTkLabel(
            row,
            text=desc,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=C_TEXT_MUTED
        ).pack(side="left")

    def _toggle_enhancer(self, val: bool):
        self.settings["ai_enhance"] = val
        settings_manager.save_settings(self.settings)
        if self.stat_enhancer_lbl:
            self.stat_enhancer_lbl.configure(text="Active" if val else "Off")

    def _toggle_startup(self, val: bool):
        win_startup.set_autostart(val)

    def _toggle_chimes(self, val: bool):
        self.settings["audio_chimes"] = val
        settings_manager.save_settings(self.settings)

    # -------------------------------------------------------------------------
    # LIFECYCLE
    # -------------------------------------------------------------------------
    def show(self):
        """Show and bring window to front."""
        if not self.root:
            self.init_ui()
        self.refresh_history()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        """Minimize to system tray."""
        if self.root:
            self.root.withdraw()


if __name__ == "__main__":
    app = DashboardUI()
    app.init_ui()
    app.show()
    app.root.mainloop()
