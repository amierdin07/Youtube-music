import os
import re
import sys
import json
import threading
import urllib.request
import io
import winsound
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk
import customtkinter as ctk

# Import modules
from downloader import YTDownloader
import ffmpeg_utils

# Regular expression to extract YouTube video ID
YOUTUBE_REGEX = re.compile(
    r'(?:https?://)?(?:www\.|m\.|music\.|studio\.)?'
    r'(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|video/|channel/[^/]+/music/detail\?(?:.*&)?v=)|youtu\.be/)'
    r'([a-zA-Z0-9_-]{11})'
)

def extract_youtube_id(url):
    match = YOUTUBE_REGEX.search(url)
    return match.group(1) if match else None

def extract_youtube_ids_from_text(text):
    matches = []
    
    # 1. Match ?v=ID or &v=ID (captures relative links like /channel/UC.../music/detail?v=ID)
    for m in re.finditer(r'[?&]v=([a-zA-Z0-9_-]{11})', text):
        vid = m.group(1)
        if vid not in matches:
            matches.append(vid)
            
    # 2. Match youtu.be/ID
    for m in re.finditer(r'youtu\.be/([a-zA-Z0-9_-]{11})', text):
        vid = m.group(1)
        if vid not in matches:
            matches.append(vid)
            
    # 3. Match shorts/ID or video/ID or embed/ID
    for m in re.finditer(r'/(?:shorts|video|embed)/([a-zA-Z0-9_-]{11})', text):
        vid = m.group(1)
        if vid not in matches:
            matches.append(vid)
            
    # 4. Match JSON track attributes like "videoId":"ID"
    for m in re.finditer(r'"videoId"\s*:\s*"([a-zA-Z0-9_-]{11})"', text):
        vid = m.group(1)
        if vid not in matches:
            matches.append(vid)
            
    return matches

def parse_creator_music_text(text):
    """
    Parses the plain text of the YouTube Creator Music page
    to extract pairs of (Track Title, Artist/Creator Name).
    """
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    tracks = []
    
    # Duration format: minutes.seconds (e.g. 4.31, 5.00, 12.04)
    duration_pattern = re.compile(r'^\d+\.\d{2}$')
    
    i = 0
    while i < len(lines):
        line = lines[i]
        if duration_pattern.match(line):
            offset = 1
            # Check if previous line is BPM (e.g. "128 BPM")
            if i - offset >= 0 and "BPM" in lines[i-offset]:
                offset += 1
            
            # Now:
            # lines[i-offset] is the mood/genres line (e.g. "Cinta, Ceria, Sedih")
            # lines[i-offset-1] is the artist name
            # lines[i-offset-2] is the track title
            artist_idx = i - offset - 1
            title_idx = i - offset - 2
            
            if title_idx >= 0 and artist_idx >= 0:
                title = lines[title_idx]
                artist = lines[artist_idx]
                
                # Filter out system header text
                noise_keywords = ["Creator Music", "Studio", "Dasbor", "Konten", "Analytics", 
                                 "Komunitas", "Bahasa", "Deteksi", "Penghasilan", "Penyesuaian", 
                                 "Setelan", "Bantuan", "Koleksi", "Baris per", "Pintasan", "Pusat"]
                                 
                is_noise = any(keyword in title or keyword in artist for keyword in noise_keywords)
                if not is_noise:
                    tracks.append({
                        'title': title,
                        'artist': artist
                    })
            i += 1
        else:
            i += 1
            
    return tracks

def clean_error_message(msg):
    if not msg:
        return "Unknown error"
    # Remove repeated "ERROR:" or "ERROR: " patterns
    while True:
        old_msg = msg
        msg = re.sub(r'^(?:ERROR|error|Error)\s*:\s*', '', msg).strip()
        if msg == old_msg:
            break
    # Remove traceback/newlines
    msg = msg.split('\n')[0]
    return msg

# Win32 Clipboard Reading Helper
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

def get_clipboard_html():
    """
    Directly query Windows Clipboard for HTML formatted data.
    This exposes hidden links (<a href="...">) and JSON script tags.
    """
    if not user32.OpenClipboard(None):
        return ""
    try:
        html_format = user32.RegisterClipboardFormatW("HTML Format")
        if not user32.IsClipboardFormatAvailable(html_format):
            return ""
        
        handle = user32.GetClipboardData(html_format)
        if not handle:
            return ""
            
        lock = kernel32.GlobalLock
        lock.argtypes = [wintypes.HANDLE]
        lock.restype = ctypes.c_void_p
        
        unlock = kernel32.GlobalUnlock
        unlock.argtypes = [wintypes.HANDLE]
        unlock.restype = wintypes.BOOL
        
        ptr = lock(handle)
        if not ptr:
            return ""
            
        try:
            html_bytes = ctypes.string_at(ptr)
            return html_bytes.decode('utf-8', errors='ignore')
        finally:
            unlock(handle)
    except Exception:
        return ""
    finally:
        user32.CloseClipboard()


class TrackCard(ctk.CTkFrame):
    def __init__(self, master, video_id, title, channel, duration, delete_callback, pause_resume_callback, **kwargs):
        super().__init__(master, fg_color="#1e1e24", border_width=1, border_color="#2d2d34", corner_radius=10, **kwargs)
        self.video_id = video_id
        self.title = title
        self.channel = channel
        self.duration = duration
        self.delete_callback = delete_callback
        self.pause_resume_callback = pause_resume_callback
        self.last_percent = 0.0
        
        self.grid_columnconfigure(0, weight=1) # Info / Title / Status
        self.grid_columnconfigure(1, weight=0) # Pause button
        self.grid_columnconfigure(2, weight=0) # Delete button
        self.grid_rowconfigure(0, weight=1)
        
        # Text details container
        self.info_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.info_frame.grid(row=0, column=0, padx=10, pady=8, sticky="nsew")
        self.info_frame.grid_columnconfigure(0, weight=1)
        
        # Title (Shortened to fit compact width)
        self.title_label = ctk.CTkLabel(self.info_frame, text=title, font=ctk.CTkFont(size=12, weight="bold"), text_color="#ffffff", anchor="w")
        self.title_label.grid(row=0, column=0, sticky="w")
        
        # Meta info
        duration_str = self.format_duration(duration)
        meta_text = f"{channel} • {duration_str}" if channel else duration_str
        self.meta_label = ctk.CTkLabel(self.info_frame, text=meta_text, font=ctk.CTkFont(size=10), text_color="#9ca3af", anchor="w")
        self.meta_label.grid(row=1, column=0, sticky="w")
        
        # Status Label
        self.status_label = ctk.CTkLabel(self.info_frame, text="Status: Queued", font=ctk.CTkFont(size=10), text_color="#38bdf8", anchor="w", wraplength=280)
        self.status_label.grid(row=2, column=0, sticky="w")
        
        # Progress Bar
        self.progress_bar = ctk.CTkProgressBar(self.info_frame, height=4, progress_color="#a855f7", fg_color="#374151")
        self.progress_bar.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.progress_bar.set(0)
        
        # Pause/Resume Button
        self.pause_btn = ctk.CTkButton(
            self, text="⏸", width=22, height=22,
            fg_color="#374151", hover_color="#4b5563",
            text_color="#ffffff", font=ctk.CTkFont(size=10, weight="bold"),
            corner_radius=11, command=self.toggle_pause_resume
        )
        self.pause_btn.grid(row=0, column=1, padx=(0, 6), pady=8, sticky="e")
        self.pause_btn.configure(state="disabled")
        
        # Delete Button (✕)
        self.delete_btn = ctk.CTkButton(
            self, text="✕", width=22, height=22, 
            fg_color="#ff1744", hover_color="#d50000",
            text_color="#ffffff", font=ctk.CTkFont(size=10, weight="bold"),
            corner_radius=11, command=lambda: self.delete_callback(self.video_id)
        )
        self.delete_btn.grid(row=0, column=2, padx=(0, 10), pady=8, sticky="e")
        
    def format_duration(self, seconds):
        if not seconds:
            return "0:00"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def toggle_pause_resume(self):
        self.pause_resume_callback(self.video_id)

    def update_progress(self, percent, speed_str, eta_str, status):
        self.last_percent = percent
        self.after(0, lambda: self._update_progress_ui(percent, speed_str, eta_str, status))
        
    def _update_progress_ui(self, percent, speed_str, eta_str, status):
        self.progress_bar.set(percent / 100.0)
        
        if status == 'Downloading':
            info_text = f"Downloading: {percent:.1f}% ({speed_str})"
            color = "#38bdf8"
            progress_color = "#38bdf8"
            self.pause_btn.configure(state="normal", text="⏸", fg_color="#374151", hover_color="#4b5563")
        elif status == 'Converting':
            info_text = "Converting to MP3..."
            color = "#c084fc"
            progress_color = "#a855f7"
            self.pause_btn.configure(state="disabled")
        elif status == 'Finalizing':
            info_text = "Finalizing..."
            color = "#fb7185"
            progress_color = "#f43f5e"
            self.pause_btn.configure(state="disabled")
        elif status == 'Success':
            info_text = "Success!"
            color = "#4ade80"
            progress_color = "#22c55e"
            self.pause_btn.grid_forget()
            self.delete_btn.configure(text="✕", fg_color="#374151", hover_color="#4b5563")
        elif status == 'Failed':
            info_text = f"Failed: {speed_str}"
            color = "#f87171"
            progress_color = "#ef4444"
            self.pause_btn.configure(state="disabled")
        elif status == 'Paused':
            info_text = f"Paused: {percent:.1f}%"
            color = "#eab308"
            progress_color = "#eab308"
            self.pause_btn.configure(state="normal", text="▶", fg_color="#10b981", hover_color="#059669")
        else:
            info_text = f"Status: {status}"
            color = "#9ca3af"
            progress_color = "#7c4dff"
            self.pause_btn.configure(state="disabled")
            
        self.status_label.configure(text=info_text, text_color=color)
        self.progress_bar.configure(progress_color=progress_color)


class YTMusicDownloaderApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Load Config
        self.config = self.load_config()
        
        # Initialize Downloader
        self.downloader = YTDownloader(cookies_browser=self.config.get("cookies_browser"))
        
        # Track storage
        self.queue = {}  # video_id -> track_info dict
        self.cards = {}  # video_id -> TrackCard object
        self.download_active = False
        
        # Window Configuration (Compact Widget Style: 1/4 of standard screen size)
        self.title("YT Music Grabber")
        self.geometry("380x630")
        self.configure(fg_color="#121214")
        self.resizable(False, True) # allow vertical resize, but keep width compact
        
        # Pin window setting
        self.is_pinned = False
        
        # Layout Config
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0) # Row 0: Header
        self.grid_rowconfigure(1, weight=0) # Row 1: Collapsible Settings Frame
        self.grid_rowconfigure(2, weight=0) # Row 2: Controls (Switches)
        self.grid_rowconfigure(3, weight=0) # Row 3: Mode Tabs (Audio vs Video)
        self.grid_rowconfigure(4, weight=0) # Row 4: Manual Paste Bar
        self.grid_rowconfigure(5, weight=0) # Row 5: Queue Header (Title + Count)
        self.grid_rowconfigure(6, weight=1) # Row 6: Queue Scroll Area (EXPANDS!)
        self.grid_rowconfigure(7, weight=0) # Row 7: Global Progress Status
        self.grid_rowconfigure(8, weight=0) # Row 8: Footer Action Buttons
        
        self.setup_header()
        self.setup_settings_panel()
        self.setup_control_panel()
        self.setup_mode_tabs()
        self.setup_manual_paste_bar()
        self.setup_queue_header()
        self.setup_queue_area()
        self.setup_footer()
        
        # Clipboard monitor loop variables
        try:
            html_content = get_clipboard_html()
            plain_content = ""
            try:
                plain_content = self.clipboard_get().strip()
            except Exception:
                pass
            self.last_clipboard_content = html_content if html_content else plain_content
        except Exception:
            self.last_clipboard_content = ""
            
        self.clipboard_monitor_active = self.config.get("auto_monitor", False)
        
        # Set switch state programmatically
        if self.clipboard_monitor_active:
            self.sidebar_monitor_switch.select()
        else:
            self.sidebar_monitor_switch.deselect()
            
        # Start monitoring loop
        self.monitor_clipboard()
        self.check_ffmpeg_status()
        
    def setup_header(self):
        self.header_frame = ctk.CTkFrame(self, fg_color="#1c1c1f", corner_radius=0, height=50)
        self.header_frame.grid(row=0, column=0, sticky="ew")
        self.header_frame.grid_columnconfigure(0, weight=1)
        self.header_frame.grid_columnconfigure(1, weight=0)
        self.header_frame.grid_columnconfigure(2, weight=0)
        
        # App Title
        self.app_title = ctk.CTkLabel(
            self.header_frame, text="YT AUDIO GRABBER", 
            font=ctk.CTkFont(size=14, weight="bold"), text_color="#00f5ff"
        )
        self.app_title.grid(row=0, column=0, padx=12, pady=10, sticky="w")
        
        # Pin Button (Always on Top toggle)
        self.pin_btn = ctk.CTkButton(
            self.header_frame, text="📌 Pin", width=55, height=26,
            fg_color="#2d2d34", hover_color="#374151", text_color="#ffffff",
            font=ctk.CTkFont(size=11), command=self.toggle_pin_window
        )
        self.pin_btn.grid(row=0, column=1, padx=6, pady=10, sticky="e")
        
        # Settings Gear Toggle Button
        self.settings_toggle_btn = ctk.CTkButton(
            self.header_frame, text="⚙️ Settings", width=80, height=26,
            fg_color="#2d2d34", hover_color="#7c4dff", text_color="#ffffff",
            font=ctk.CTkFont(size=11), command=self.toggle_settings_panel
        )
        self.settings_toggle_btn.grid(row=0, column=2, padx=12, pady=10, sticky="e")
        
    def setup_settings_panel(self):
        # Settings frame is collapsed initially
        self.settings_panel = ctk.CTkFrame(self, fg_color="#1c1c1f", corner_radius=0, border_width=1, border_color="#2d2d34")
        self.settings_panel.grid_columnconfigure(0, weight=1)
        self.settings_visible = False
        
        # Format Options
        self.fmt_lbl = ctk.CTkLabel(self.settings_panel, text="Output Format:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#9ca3af")
        self.fmt_lbl.grid(row=0, column=0, padx=15, pady=(8, 2), sticky="w")
        
        self.format_menu = ctk.CTkOptionMenu(
            self.settings_panel, values=["M4A Audio (Fast, No FFmpeg)", "MP3 Audio (High Quality)", "MP4 Video (Fast, Multi-threaded)"],
            fg_color="#121214", button_color="#2d2d34", button_hover_color="#374151",
            dropdown_fg_color="#1c1c1f", font=ctk.CTkFont(size=12), command=self.on_format_changed
        )
        self.format_menu.grid(row=1, column=0, padx=15, pady=(0, 8), sticky="ew")
        
        fmt = self.config.get("audio_format", "M4A")
        if fmt == "MP3":
            self.format_menu.set("MP3 Audio (High Quality)")
        elif fmt == "MP4":
            self.format_menu.set("MP4 Video (Fast, Multi-threaded)")
        else:
            self.format_menu.set("M4A Audio (Fast, No FFmpeg)")
            
        # Video Resolution Dropdown (only visible/enabled when video mode is active)
        self.video_res_lbl = ctk.CTkLabel(self.settings_panel, text="Video Resolution:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#9ca3af")
        
        self.video_res_menu = ctk.CTkOptionMenu(
            self.settings_panel, values=["1080p", "720p", "480p", "360p", "Best Quality"],
            fg_color="#121214", button_color="#2d2d34", button_hover_color="#374151",
            dropdown_fg_color="#1c1c1f", font=ctk.CTkFont(size=12), command=self.on_video_res_changed
        )
        self.video_res_menu.set(self.config.get("video_resolution", "Best Quality"))
            
        # Cookies Browser Dropdown
        self.cookies_lbl = ctk.CTkLabel(self.settings_panel, text="Cookies Auth (YouTube Studio):", font=ctk.CTkFont(size=11, weight="bold"), text_color="#9ca3af")
        self.cookies_lbl.grid(row=4, column=0, padx=15, pady=(0, 2), sticky="w")
        
        self.cookies_menu = ctk.CTkOptionMenu(
            self.settings_panel, values=["None", "Chrome", "Firefox", "Edge", "Brave"],
            fg_color="#121214", button_color="#2d2d34", button_hover_color="#374151",
            dropdown_fg_color="#1c1c1f", font=ctk.CTkFont(size=12), command=self.on_cookies_changed
        )
        self.cookies_menu.grid(row=5, column=0, padx=15, pady=(0, 8), sticky="ew")
        self.cookies_menu.set(self.config.get("cookies_browser", "None"))
        
        # Download Folder row
        self.dir_lbl = ctk.CTkLabel(self.settings_panel, text="Save Folder:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#9ca3af")
        self.dir_lbl.grid(row=6, column=0, padx=15, pady=(0, 2), sticky="w")
        
        self.folder_frame = ctk.CTkFrame(self.settings_panel, fg_color="transparent")
        self.folder_frame.grid(row=7, column=0, padx=15, pady=(0, 8), sticky="ew")
        self.folder_frame.grid_columnconfigure(0, weight=1)
        
        self.folder_path_lbl = ctk.CTkLabel(
            self.folder_frame, text=self.shorten_path(self.config.get("download_dir")), 
            font=ctk.CTkFont(size=11), text_color="#e5e7eb", anchor="w"
        )
        self.folder_path_lbl.grid(row=0, column=0, sticky="ew")
        
        self.folder_browse_btn = ctk.CTkButton(
            self.folder_frame, text="Browse", width=60, height=22,
            fg_color="#2d2d34", hover_color="#374151", text_color="#ffffff",
            font=ctk.CTkFont(size=11), command=self.browse_download_dir
        )
        self.folder_browse_btn.grid(row=0, column=1, padx=(6, 0), sticky="e")
        
        # FFmpeg frame
        self.ffmpeg_frame = ctk.CTkFrame(self.settings_panel, fg_color="#121214", border_width=1, border_color="#2d2d34", corner_radius=6)
        self.ffmpeg_frame.grid(row=8, column=0, padx=15, pady=(0, 10), sticky="ew")
        self.ffmpeg_frame.grid_columnconfigure(0, weight=1)
        
        self.ffmpeg_status_lbl = ctk.CTkLabel(
            self.ffmpeg_frame, text="FFmpeg Status: Checking...", 
            font=ctk.CTkFont(size=10), text_color="#9ca3af"
        )
        self.ffmpeg_status_lbl.grid(row=0, column=0, padx=8, pady=6, sticky="w")
        
        self.ffmpeg_btn = ctk.CTkButton(
            self.ffmpeg_frame, text="Install", width=50, height=18,
            fg_color="#00adb5", hover_color="#008080", text_color="#ffffff",
            font=ctk.CTkFont(size=10, weight="bold"), command=self.trigger_ffmpeg_download
        )
        self.update_settings_visibility()
        
    def setup_control_panel(self):
        self.controls_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.controls_frame.grid(row=2, column=0, sticky="ew", padx=15, pady=12)
        self.controls_frame.grid_columnconfigure(0, weight=1)
        self.controls_frame.grid_columnconfigure(1, weight=1)
        
        # Auto Capture Switch
        self.sidebar_monitor_switch = ctk.CTkSwitch(
            self.controls_frame, text="Auto-Capture", 
            font=ctk.CTkFont(size=12), progress_color="#00f5ff",
            command=self.toggle_clipboard_monitor
        )
        self.sidebar_monitor_switch.grid(row=0, column=0, sticky="w")
            
        # Auto Download Switch
        self.auto_download_switch = ctk.CTkSwitch(
            self.controls_frame, text="Auto-Download", 
            font=ctk.CTkFont(size=12), progress_color="#a855f7",
            command=self.toggle_auto_download
        )
        self.auto_download_switch.grid(row=0, column=1, sticky="e")
        if self.config.get("auto_download", False):
            self.auto_download_switch.select()
        else:
            self.auto_download_switch.deselect()

    def setup_mode_tabs(self):
        self.tab_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.tab_frame.grid(row=3, column=0, sticky="ew", padx=15, pady=(0, 10))
        self.tab_frame.grid_columnconfigure(0, weight=1)
        
        # Segmented button as tabs
        self.mode_tab = ctk.CTkSegmentedButton(
            self.tab_frame,
            values=["🎵 Download Audio", "🎥 Download Video"],
            command=self.on_mode_tab_changed,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#1c1c1f",
            selected_color="#7c4dff",
            selected_hover_color="#651fff",
            text_color="#ffffff"
        )
        self.mode_tab.grid(row=0, column=0, sticky="ew")
        
        # Set initial value based on config
        current_fmt = self.config.get("audio_format", "M4A")
        if current_fmt == "MP4":
            self.mode_tab.set("🎥 Download Video")
        else:
            self.mode_tab.set("🎵 Download Audio")

    def on_mode_tab_changed(self, value):
        if value == "🎥 Download Video":
            # Set to MP4 Video mode
            self.config["audio_format"] = "MP4"
            # Update settings dropdown if visible
            self.format_menu.set("MP4 Video (Fast, Multi-threaded)")
            
            # Check FFmpeg
            if not ffmpeg_utils.is_ffmpeg_available():
                messagebox.showwarning(
                    "FFmpeg Recommended", 
                    "You selected Video mode, but FFmpeg is not installed.\n\n"
                    "Without FFmpeg, video download will be limited to 720p or lower.\n"
                    "Please install FFmpeg via Settings -> Install for high quality video merging."
                )
        else:
            # Revert to last audio format (either MP3 or default to M4A)
            current_settings_fmt = self.format_menu.get()
            if "MP3" in current_settings_fmt:
                self.config["audio_format"] = "MP3"
                self.format_menu.set("MP3 Audio (High Quality)")
            else:
                self.config["audio_format"] = "M4A"
                self.format_menu.set("M4A Audio (Fast, No FFmpeg)")
                
        self.last_clipboard_content = ""
        self.save_config()
        self.show_toast(f"Mode switched: {value}")
        self.update_settings_visibility()

    def setup_manual_paste_bar(self):
        self.paste_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.paste_bar.grid(row=4, column=0, sticky="ew", padx=15, pady=(0, 10))
        self.paste_bar.grid_columnconfigure(0, weight=1)
        
        self.manual_entry = ctk.CTkEntry(
            self.paste_bar, placeholder_text="Paste YouTube link manually...",
            fg_color="#1c1c1f", border_color="#2d2d34", height=28, font=ctk.CTkFont(size=11)
        )
        self.manual_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.manual_entry.bind("<Return>", lambda e: self.add_manual_link())
        
        self.manual_add_btn = ctk.CTkButton(
            self.paste_bar, text="Add", width=45, height=28,
            fg_color="#7c4dff", hover_color="#651fff", text_color="#ffffff",
            font=ctk.CTkFont(size=11, weight="bold"), command=self.add_manual_link
        )
        self.manual_add_btn.grid(row=0, column=1, sticky="e")

    def setup_queue_header(self):
        self.queue_header = ctk.CTkFrame(self, fg_color="transparent")
        self.queue_header.grid(row=5, column=0, sticky="ew", padx=15, pady=(0, 4))
        self.queue_header.grid_columnconfigure(0, weight=1)
        
        self.queue_count_lbl = ctk.CTkLabel(
            self.queue_header, text="Queue (0 items)", 
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#9ca3af"
        )
        self.queue_count_lbl.grid(row=0, column=0, sticky="w")

    def setup_queue_area(self):
        # Scroll area for cards (placed in Row 6)
        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="#121214", label_text="")
        self.scroll_frame.grid(row=6, column=0, sticky="nsew", padx=12, pady=(0, 10))
        
        # Empty State
        self.empty_state_frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        self.empty_state_frame.pack(expand=True, fill="both", pady=60)
        
        self.empty_state_lbl = ctk.CTkLabel(
            self.empty_state_frame, 
            text="Queue is empty\n\n💡 Tips: Press Ctrl+A & Ctrl+C on the\nChrome page to copy all tracks!",
            font=ctk.CTkFont(size=11), text_color="#6b7280"
        )
        self.empty_state_lbl.pack()
        
        # Toast notifications overlay
        self.toast_label = ctk.CTkLabel(
            self, text="", fg_color="#7c4dff", text_color="#ffffff",
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=15, height=28
        )
        
    def setup_footer(self):
        # Global Progress (Hidden until download starts - gridded dynamically)
        self.global_progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.global_progress_frame.grid_columnconfigure(0, weight=1)
        
        self.global_progress_lbl = ctk.CTkLabel(
            self.global_progress_frame, text="Starting...", 
            font=ctk.CTkFont(size=11), text_color="#9ca3af"
        )
        self.global_progress_lbl.grid(row=0, column=0, sticky="w", padx=15)
        
        self.global_progress_bar = ctk.CTkProgressBar(self.global_progress_frame, height=5, progress_color="#00f5ff", fg_color="#1c1c1f")
        self.global_progress_bar.grid(row=1, column=0, sticky="ew", padx=15, pady=(2, 8))
        self.global_progress_bar.set(0)
        
        # Action Buttons (placed in Row 8)
        self.footer_actions = ctk.CTkFrame(self, fg_color="#1c1c1f", height=55, corner_radius=0)
        self.footer_actions.grid(row=8, column=0, sticky="ew")
        self.footer_actions.grid_columnconfigure(0, weight=1)
        self.footer_actions.grid_columnconfigure(1, weight=1)
        
        self.clear_btn = ctk.CTkButton(
            self.footer_actions, text="Clear Queue", height=35,
            fg_color="#2d2d34", hover_color="#ef4444", text_color="#ffffff",
            font=ctk.CTkFont(size=12, weight="bold"), command=self.clear_queue
        )
        self.clear_btn.grid(row=0, column=0, padx=(12, 6), pady=10, sticky="ew")
        
        self.download_btn = ctk.CTkButton(
            self.footer_actions, text="Download All", height=35,
            fg_color="#00f5ff", hover_color="#00ced1", text_color="#121214",
            font=ctk.CTkFont(size=12, weight="bold"), command=self.start_batch_download
        )
        self.download_btn.grid(row=0, column=1, padx=(6, 12), pady=10, sticky="ew")

    # --- Collapsible Panel & Pinning ---
    def toggle_settings_panel(self):
        if self.settings_visible:
            self.settings_panel.grid_forget()
            self.settings_visible = False
            self.settings_toggle_btn.configure(fg_color="#2d2d34", text="⚙️ Settings")
        else:
            self.settings_panel.grid(row=1, column=0, sticky="ew")
            self.settings_visible = True
            self.settings_toggle_btn.configure(fg_color="#7c4dff", text="⚙️ Close")
            
    def toggle_pin_window(self):
        self.is_pinned = not self.is_pinned
        self.attributes("-topmost", self.is_pinned)
        if self.is_pinned:
            self.pin_btn.configure(fg_color="#00f5ff", text="📌 Pinned", text_color="#121214")
            self.show_toast("Window pinned on top")
        else:
            self.pin_btn.configure(fg_color="#2d2d34", text="📌 Pin", text_color="#ffffff")
            self.show_toast("Window unpinned")

    # --- Config Management ---
    def load_config(self):
        default_dir = os.path.join(os.path.expanduser('~'), 'Downloads')
        default_config = {
            "download_dir": default_dir,
            "audio_format": "M4A",
            "cookies_browser": "None",
            "auto_monitor": False,
            "auto_download": False,
            "video_resolution": "Best Quality"
        }
        config_path = os.path.join(ffmpeg_utils.get_app_dir(), "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                    for k, v in default_config.items():
                        if k not in config:
                            config[k] = v
                    return config
            except Exception:
                return default_config
        return default_config
        
    def save_config(self):
        config_path = os.path.join(ffmpeg_utils.get_app_dir(), "config.json")
        try:
            with open(config_path, "w") as f:
                json.dump(self.config, f, indent=4)
        except Exception:
            pass

    def shorten_path(self, path):
        if len(path) > 35:
            return path[:15] + "..." + path[-17:]
        return path

    # --- Settings Events ---
    def check_ffmpeg_status(self):
        if ffmpeg_utils.is_ffmpeg_available():
            self.ffmpeg_status_lbl.configure(text="FFmpeg: Detected & Ready", text_color="#4ade80")
            self.ffmpeg_btn.grid_forget()
        else:
            self.ffmpeg_status_lbl.configure(text="FFmpeg: Missing (MP3 disabled)", text_color="#f87171")
            self.ffmpeg_btn.grid(row=0, column=1, padx=8, pady=6, sticky="e")
            
    def trigger_ffmpeg_download(self):
        self.ffmpeg_btn.grid_forget()
        self.ffmpeg_status_lbl.configure(text="FFmpeg: Downloading...", text_color="#38bdf8")
        
        def run():
            def callback(percent, text):
                self.after(0, lambda: self.ffmpeg_status_lbl.configure(text=f"FFmpeg: {text}"))
            
            success = ffmpeg_utils.download_ffmpeg(progress_callback=callback)
            if success:
                self.after(0, self.check_ffmpeg_status)
                self.after(0, lambda: self.show_toast("FFmpeg downloaded!"))
            else:
                self.after(0, self.check_ffmpeg_status)
                self.after(0, lambda: messagebox.showerror("FFmpeg Error", "Failed to download FFmpeg. Please place ffmpeg.exe manually or use M4A."))
                
        threading.Thread(target=run, daemon=True).start()

    def browse_download_dir(self):
        selected = filedialog.askdirectory(initialdir=self.config.get("download_dir"))
        if selected:
            selected_path = os.path.normpath(selected)
            self.config["download_dir"] = selected_path
            self.folder_path_lbl.configure(text=self.shorten_path(selected_path))
            self.save_config()

    def on_format_changed(self, value):
        if "MP3" in value:
            self.config["audio_format"] = "MP3"
            if hasattr(self, 'mode_tab'):
                self.mode_tab.set("🎵 Download Audio")
            if not ffmpeg_utils.is_ffmpeg_available():
                messagebox.showwarning(
                    "FFmpeg Required", 
                    "You selected MP3 format, but FFmpeg is not installed.\n\n"
                    "Please install FFmpeg via Settings -> Install, or use M4A."
                )
        elif "MP4" in value:
            self.config["audio_format"] = "MP4"
            if hasattr(self, 'mode_tab'):
                self.mode_tab.set("🎥 Download Video")
            if not ffmpeg_utils.is_ffmpeg_available():
                messagebox.showwarning(
                    "FFmpeg Recommended", 
                    "You selected MP4 Video format, but FFmpeg is not installed.\n\n"
                    "Without FFmpeg, video download will be limited to 720p or lower.\n"
                    "Please install FFmpeg via Settings -> Install for high quality video merging."
                )
        else:
            self.config["audio_format"] = "M4A"
            if hasattr(self, 'mode_tab'):
                self.mode_tab.set("🎵 Download Audio")
        self.last_clipboard_content = ""
        self.save_config()
        self.update_settings_visibility()

    def on_video_res_changed(self, value):
        self.config["video_resolution"] = value
        self.save_config()
        self.show_toast(f"Resolution set to: {value}")

    def update_settings_visibility(self):
        if not hasattr(self, 'video_res_lbl') or not hasattr(self, 'video_res_menu'):
            return
        current_fmt = self.config.get("audio_format", "M4A")
        if current_fmt == "MP4":
            self.video_res_lbl.grid(row=2, column=0, padx=15, pady=(0, 2), sticky="w")
            self.video_res_menu.grid(row=3, column=0, padx=15, pady=(0, 8), sticky="ew")
        else:
            self.video_res_lbl.grid_forget()
            self.video_res_menu.grid_forget()

    def on_cookies_changed(self, value):
        self.config["cookies_browser"] = value
        self.downloader.set_cookies_browser(value)
        self.last_clipboard_content = ""
        self.save_config()

    def toggle_clipboard_monitor(self):
        self.clipboard_monitor_active = self.sidebar_monitor_switch.get() == 1
        self.config["auto_monitor"] = self.clipboard_monitor_active
        self.save_config()
        if self.clipboard_monitor_active:
            self.last_clipboard_content = ""
            self.show_toast("Auto-Capture active")
        else:
            self.show_toast("Auto-Capture disabled")
            
    def toggle_auto_download(self):
        active = self.auto_download_switch.get() == 1
        self.config["auto_download"] = active
        self.save_config()
        if active:
            self.show_toast("Auto-Download enabled")
        else:
            self.show_toast("Auto-Download disabled")

    # --- Toast Notifications ---
    def show_toast(self, text):
        self.toast_label.configure(text=f"  {text}  ")
        self.toast_label.place(relx=0.5, rely=0.85, anchor="center")
        self.after(2000, self.toast_label.place_forget)

    # --- Clipboard Monitoring Loop ---
    def monitor_clipboard(self):
        if self.clipboard_monitor_active and not self.download_active:
            try:
                # 1. First try reading raw HTML format from clipboard (for rich links)
                html_content = get_clipboard_html()
                
                # 2. Fallback to standard plain text if HTML not available
                plain_content = ""
                try:
                    plain_content = self.clipboard_get().strip()
                except Exception:
                    pass
                
                content_to_check = html_content if html_content else plain_content
                
                if content_to_check and content_to_check != self.last_clipboard_content:
                    self.last_clipboard_content = content_to_check
                    
                    # Debug logging of captured clipboard
                    debug_path = os.path.join(ffmpeg_utils.get_app_dir(), "clipboard_debug.txt")
                    try:
                        with open(debug_path, "w", encoding="utf-8") as f:
                            f.write(f"--- PLAIN TEXT LENGTH: {len(plain_content)} ---\n")
                            f.write(plain_content)
                            f.write(f"\n\n--- HTML LENGTH: {len(html_content)} ---\n")
                            f.write(html_content)
                    except Exception:
                        pass
                    
                    # Try direct URL/ID extraction first
                    video_ids = extract_youtube_ids_from_text(content_to_check)
                    if video_ids:
                        added_count = 0
                        for video_id in video_ids:
                            url = f"https://www.youtube.com/watch?v={video_id}"
                            if video_id not in self.queue:
                                self.after(0, lambda u=url, vid=video_id: self.add_to_queue_by_url_silent(u, vid))
                                added_count += 1
                                
                        if added_count > 0:
                            try:
                                winsound.MessageBeep(winsound.MB_ICONASTERISK)
                            except Exception:
                                pass
                            self.show_toast(f"Captured {added_count} tracks!")
                    else:
                        # Fallback: Parse Plain Text for Title + Artist rows
                        tracks = parse_creator_music_text(plain_content)
                        if tracks:
                            added_count = 0
                            for t in tracks:
                                search_key = f"search_{t['title']}_{t['artist']}".lower().replace(" ", "_")
                                if search_key not in self.queue:
                                    self.after(0, lambda title=t['title'], artist=t['artist'], key=search_key: self.add_search_track_to_queue(title, artist, key))
                                    added_count += 1
                                    
                            if added_count > 0:
                                try:
                                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
                                except Exception:
                                    pass
                                self.show_toast(f"Found {added_count} tracks on page!")
            except Exception:
                pass
        self.after(500, self.monitor_clipboard)

    def add_manual_link(self):
        url_text = self.manual_entry.get().strip()
        if not url_text:
            return
            
        video_ids = extract_youtube_ids_from_text(url_text)
        if not video_ids:
            messagebox.showerror("Invalid Link", "Please enter a valid YouTube link.")
            return
            
        self.manual_entry.delete(0, tk.END)
        
        added_count = 0
        for video_id in video_ids:
            url = f"https://www.youtube.com/watch?v={video_id}"
            if video_id not in self.queue:
                self.add_to_queue_by_url_silent(url, video_id)
                added_count += 1
                
        if added_count > 0:
            self.show_toast(f"Added {added_count} tracks manually!")

    # --- Queue Logic ---
    def add_to_queue_by_url_silent(self, url, video_id):
        if video_id in self.queue:
            return
            
        if len(self.queue) == 0:
            self.empty_state_frame.pack_forget()
            
        track_info = {
            'url': url,
            'video_id': video_id,
            'title': "Fetching information...",
            'channel': "",
            'duration': 0,
            'status': "Fetching Info..."
        }
        self.queue[video_id] = track_info
        
        # Track card (No thumbnail for compact space)
        card = TrackCard(
            self.scroll_frame, video_id, track_info['title'], 
            track_info['channel'], track_info['duration'], 
            self.remove_from_queue, self.handle_pause_resume
        )
        card.pack(fill="x", padx=6, pady=4)
        card.status_label.configure(text="Fetching details...", text_color="#eab308")
        self.cards[video_id] = card
        self.update_queue_count()
        
        # Async metadata fetching
        def info_callback(info, error):
            if error:
                self.after(0, lambda: self.handle_metadata_error(video_id, error))
            else:
                self.after(0, lambda: self.handle_metadata_success(video_id, info))
                
        self.downloader.get_info(url, info_callback)

    def add_search_track_to_queue(self, title, artist, key):
        if key in self.queue:
            return
            
        if len(self.queue) == 0:
            self.empty_state_frame.pack_forget()
            
        track_info = {
            'url': f"ytsearch1:{title} {artist}",
            'video_id': key,
            'title': title,
            'channel': artist,
            'duration': 0,
            'status': "Searching YouTube..."
        }
        self.queue[key] = track_info
        
        card = TrackCard(
            self.scroll_frame, key, track_info['title'], 
            track_info['channel'], track_info['duration'], 
            self.remove_from_queue, self.handle_pause_resume
        )
        card.pack(fill="x", padx=6, pady=4)
        card.status_label.configure(text="Searching YouTube...", text_color="#eab308")
        self.cards[key] = card
        self.update_queue_count()
        
        def info_callback(info, error):
            if error:
                self.after(0, lambda: self.handle_metadata_error(key, error))
            else:
                self.after(0, lambda: self.handle_search_success(key, info))
                
        self.downloader.get_info(track_info['url'], info_callback)

    def handle_metadata_success(self, video_id, info):
        if video_id not in self.queue:
            return
            
        self.queue[video_id].update(info)
        self.queue[video_id]['status'] = 'Queued'
        
        card = self.cards[video_id]
        
        # Shorten Title
        display_title = info['title']
        if len(display_title) > 36:
            display_title = display_title[:33] + "..."
        card.title_label.configure(text=display_title)
        
        duration_str = card.format_duration(info['duration'])
        channel_info = f"{info['channel']} • {duration_str}" if info['channel'] else duration_str
        card.meta_label.configure(text=channel_info)
        
        card.status_label.configure(text="Status: Queued", text_color="#38bdf8")
        
        # AUTO DOWNLOAD Trigger
        if self.config.get("auto_download", False) and not self.download_active:
            self.start_single_download(video_id)

    def handle_search_success(self, key, info):
        if key not in self.queue:
            return
            
        self.queue[key].update({
            'url': info['url'],
            'title': info['title'],
            'channel': info['channel'],
            'duration': info['duration'],
            'status': 'Queued'
        })
        
        card = self.cards[key]
        display_title = info['title']
        if len(display_title) > 36:
            display_title = display_title[:33] + "..."
        card.title_label.configure(text=display_title)
        
        duration_str = card.format_duration(info['duration'])
        channel_info = f"{info['channel']} • {duration_str}" if info['channel'] else duration_str
        card.meta_label.configure(text=channel_info)
        
        card.status_label.configure(text="Status: Queued", text_color="#38bdf8")
        
        if self.config.get("auto_download", False) and not self.download_active:
            self.start_single_download(key)
            
    def handle_metadata_error(self, video_id, error):
        if video_id not in self.queue:
            return
        self.queue[video_id]['status'] = 'Failed'
        card = self.cards[video_id]
        card.title_label.configure(text="Load failed")
        card.meta_label.configure(text="Verify link/cookies in settings.")
        cleaned_err = clean_error_message(error)
        card.update_progress(0, cleaned_err[:100], "", "Failed")

    def remove_from_queue(self, video_id):
        if self.download_active:
            status = self.queue.get(video_id, {}).get('status', '')
            if status in ['Downloading', 'Converting', 'Finalizing']:
                self.show_toast("Cannot remove active download")
                return
                
        if video_id in self.cards:
            self.cards[video_id].pack_forget()
            self.cards[video_id].destroy()
            del self.cards[video_id]
            
        if video_id in self.queue:
            del self.queue[video_id]
            
        self.update_queue_count()
        if len(self.queue) == 0:
            self.empty_state_frame.pack(expand=True, fill="both", pady=60)
            self.hide_global_progress()
            self.last_clipboard_content = ""
            
    def clear_queue(self):
        if self.download_active:
            self.show_toast("Cannot clear during download")
            return
        for video_id in list(self.cards.keys()):
            self.remove_from_queue(video_id)
            
    def update_queue_count(self):
        count = len(self.queue)
        self.queue_count_lbl.configure(text=f"Queue ({count} items)")

    def handle_pause_resume(self, video_id):
        track = self.queue.get(video_id)
        if not track:
            return
            
        status = track.get('status')
        if status == 'Downloading':
            self.downloader.pause_video(video_id)
            if video_id in self.cards:
                self.cards[video_id].status_label.configure(text="Pausing...", text_color="#fb7185")
                self.cards[video_id].pause_btn.configure(state="disabled")
        elif status == 'Paused':
            self.downloader.resume_video(video_id)
            self.start_single_download(video_id)

    # --- Downloading Engines ---
    def start_single_download(self, video_id):
        dl_dir = self.config.get("download_dir")
        fmt = self.config.get("audio_format", "M4A")
        ffmpeg_path = None
        if fmt in ["MP3", "MP4"]:
            if ffmpeg_utils.is_ffmpeg_available():
                ffmpeg_path = ffmpeg_utils.get_ffmpeg_dir()
            elif fmt == "MP3":
                self.show_toast("FFmpeg missing! Fallback to M4A.")
                fmt = "M4A"
                
        track_info = self.queue.get(video_id)
        if not track_info or track_info['status'] == 'Success':
            return
            
        track_info['status'] = 'Downloading'
        self.cards[video_id].update_progress(0, "Connecting...", "", "Downloading")
        
        self.show_global_progress()
        self.global_progress_lbl.configure(text=f"Downloading {track_info['title'][:25]}...")
        self.global_progress_bar.set(0.2)
        
        def on_progress(vid, percent, speed_str, eta_str, status):
            if vid in self.cards:
                self.cards[vid].update_progress(percent, speed_str, eta_str, status)
                if vid == video_id:
                    self.global_progress_bar.set(percent / 100.0)
                    self.global_progress_lbl.configure(text=f"Downloading... {percent:.1f}%")
                    
        def on_completed(vid, success, error_msg):
            if error_msg == "Paused":
                status = 'Paused'
            else:
                status = 'Success' if success else 'Failed'
                
            if vid in self.queue:
                self.queue[vid]['status'] = status
            if vid in self.cards:
                if status == 'Paused':
                    self.cards[vid].update_progress(self.cards[vid].last_percent, "Paused", "", "Paused")
                else:
                    cleaned_err = clean_error_message(error_msg)
                    self.cards[vid].update_progress(100.0 if success else 0.0, "" if success else cleaned_err[:100], "", status)
                
            self.after(0, self.hide_global_progress)
            if status != 'Paused':
                try:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                except Exception:
                    pass
                self.after(0, lambda: self.show_toast("Download completed!" if success else "Download failed!"))
            
        self.downloader.download_track(
            track_info, dl_dir, fmt, ffmpeg_path, 
            on_progress, on_completed,
            video_resolution=self.config.get("video_resolution", "Best Quality")
        )

    def start_batch_download(self):
        if self.download_active:
            self.show_toast("Downloading running!")
            return
            
        valid_tracks = [vid for vid, track in self.queue.items() if track['status'] not in ['Success', 'FailedInfo']]
        if not valid_tracks:
            messagebox.showwarning("Empty Queue", "No tracks ready to download.")
            return
            
        dl_dir = self.config.get("download_dir")
        if not os.path.exists(dl_dir):
            try:
                os.makedirs(dl_dir)
            except Exception as e:
                messagebox.showerror("Error", f"Folder error: {str(e)}")
                return
                
        fmt = self.config.get("audio_format", "M4A")
        ffmpeg_path = None
        if fmt in ["MP3", "MP4"]:
            if ffmpeg_utils.is_ffmpeg_available():
                ffmpeg_path = ffmpeg_utils.get_ffmpeg_dir()
            elif fmt == "MP3":
                messagebox.showerror("FFmpeg Missing", "Please install FFmpeg or switch to M4A.")
                return

        self.download_active = True
        self.disable_controls()
        self.show_global_progress()
        
        total = len(valid_tracks)
        completed = 0
        
        def on_progress(vid, percent, speed_str, eta_str, status):
            if vid in self.cards:
                self.cards[vid].update_progress(percent, speed_str, eta_str, status)
                
        def on_completed(vid, success, error_msg):
            nonlocal completed
            completed += 1
            if error_msg == "Paused":
                status = 'Paused'
            else:
                status = 'Success' if success else 'Failed'
                
            if vid in self.queue:
                self.queue[vid]['status'] = status
            if vid in self.cards:
                if status == 'Paused':
                    self.cards[vid].update_progress(self.cards[vid].last_percent, "Paused", "", "Paused")
                else:
                    cleaned_err = clean_error_message(error_msg)
                    self.cards[vid].update_progress(100.0 if success else 0.0, "" if success else cleaned_err[:100], "", status)
                
            self.after(0, lambda: self.global_progress_bar.set(completed / total))
            self.after(0, lambda: self.global_progress_lbl.configure(text=f"Downloaded {completed} of {total}..."))
            
            if completed >= total:
                self.after(0, self.finish_batch_download)

        for vid in valid_tracks:
            track = self.queue[vid]
            track['status'] = 'Downloading'
            self.cards[vid].update_progress(0, "Connecting...", "", "Downloading")
            self.downloader.download_track(
                track, dl_dir, fmt, ffmpeg_path, 
                on_progress, on_completed,
                video_resolution=self.config.get("video_resolution", "Best Quality")
            )
            
    def finish_batch_download(self):
        self.download_active = False
        self.enable_controls()
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        self.global_progress_lbl.configure(text="Completed!")
        self.show_toast("Queue download complete!")
        
    def disable_controls(self):
        self.download_btn.configure(state="disabled", text="Running...")
        self.clear_btn.configure(state="disabled")
        self.manual_add_btn.configure(state="disabled")
        self.manual_entry.configure(state="disabled")
        self.format_menu.configure(state="disabled")
        self.cookies_menu.configure(state="disabled")
        self.folder_browse_btn.configure(state="disabled")
        self.sidebar_monitor_switch.configure(state="disabled")
        self.auto_download_switch.configure(state="disabled")
        
    def enable_controls(self):
        self.download_btn.configure(state="normal", text="Download All")
        self.clear_btn.configure(state="normal")
        self.manual_add_btn.configure(state="normal")
        self.manual_entry.configure(state="normal")
        self.format_menu.configure(state="normal")
        self.cookies_menu.configure(state="normal")
        self.folder_browse_btn.configure(state="normal")
        self.sidebar_monitor_switch.configure(state="normal")
        self.auto_download_switch.configure(state="normal")

    def show_global_progress(self):
        self.global_progress_frame.grid(row=7, column=0, sticky="ew", pady=(0, 6))
        self.global_progress_bar.set(0)
        self.global_progress_lbl.configure(text="Preparing...")
        
    def hide_global_progress(self):
        self.global_progress_frame.grid_forget()

if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    
    app = YTMusicDownloaderApp()
    app.mainloop()
