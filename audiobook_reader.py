"""
For dekstop , not working in browser
"""

import re
import threading
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pyttsx3
from pypdf import PdfReader


class AudiobookReader:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF Audiobook Reader")
        self.root.geometry("800x600")

        self.pdf_path = None
        self.full_text = ""
        self.sentences = []          # list of (start_idx, end_idx, text)
        self.engine = None
        self.speak_thread = None
        self.stop_requested = False
        self.ui_queue = queue.Queue()  # thread -> main thread messages

        self._build_ui()
        self._poll_queue()

    # ---------------- UI ----------------
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        self.upload_btn = ttk.Button(top, text="Upload PDF", command=self.upload_pdf)
        self.upload_btn.pack(side="left", padx=(0, 8))

        self.run_btn = ttk.Button(top, text="Run", command=self.run_reading, state="disabled")
        self.run_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = ttk.Button(top, text="Stop", command=self.stop_reading, state="disabled")
        self.stop_btn.pack(side="left", padx=(0, 8))

        self.file_label = ttk.Label(top, text="No file selected")
        self.file_label.pack(side="left", padx=(10, 0))

        # Rate / voice controls
        controls = ttk.Frame(self.root, padding=(10, 0))
        controls.pack(fill="x")

        ttk.Label(controls, text="Speed:").pack(side="left")
        self.rate_var = tk.IntVar(value=170)
        rate_scale = ttk.Scale(controls, from_=80, to=300, variable=self.rate_var, orient="horizontal")
        rate_scale.pack(side="left", padx=(5, 15), fill="x", expand=True)

        # Text display
        text_frame = ttk.Frame(self.root, padding=10)
        text_frame.pack(fill="both", expand=True)

        self.text_widget = tk.Text(text_frame, wrap="word", font=("Georgia", 13))
        self.text_widget.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(text_frame, command=self.text_widget.yview)
        scrollbar.pack(side="right", fill="y")
        self.text_widget.configure(yscrollcommand=scrollbar.set)

        self.text_widget.tag_configure("highlight", background="#ffef7a")
        self.text_widget.configure(state="disabled")

        self.status_var = tk.StringVar(value="Ready.")
        status = ttk.Label(self.root, textvariable=self.status_var, padding=(10, 5))
        status.pack(fill="x")

    # ---------------- File handling ----------------
    def upload_pdf(self):
        path = filedialog.askopenfilename(
            title="Select a PDF",
            filetypes=[("PDF files", "*.pdf")]
        )
        if not path:
            return

        self.pdf_path = path
        self.file_label.config(text=path.split("/")[-1])
        self.status_var.set("Extracting text from PDF...")
        self.root.update_idletasks()

        try:
            reader = PdfReader(path)
            text_parts = []
            for page in reader.pages:
                extracted = page.extract_text() or ""
                text_parts.append(extracted)
            self.full_text = "\n".join(text_parts).strip()
        except Exception as e:
            messagebox.showerror("Error reading PDF", str(e))
            self.status_var.set("Failed to read PDF.")
            return

        if not self.full_text:
            messagebox.showwarning("No text found", "Could not extract any text from this PDF.")
            self.status_var.set("No text found in PDF.")
            return

        self._load_text_into_widget(self.full_text)
        self._split_sentences(self.full_text)
        self.run_btn.config(state="normal")
        self.status_var.set(f"Loaded {len(reader.pages)} page(s). Ready to read.")

    def _load_text_into_widget(self, text):
        self.text_widget.configure(state="normal")
        self.text_widget.delete("1.0", "end")
        self.text_widget.insert("1.0", text)
        self.text_widget.configure(state="disabled")

    def _split_sentences(self, text):
        """Break text into (start, end, sentence) spans for fallback highlighting."""
        self.sentences = []
        for match in re.finditer(r"[^.!?]+[.!?]*", text):
            s, e = match.span()
            sentence = match.group().strip()
            if sentence:
                self.sentences.append((s, e, sentence))

    # ---------------- Reading control ----------------
    def run_reading(self):
        if not self.full_text:
            return
        self.stop_requested = False
        self.run_btn.config(state="disabled")
        self.upload_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set("Reading...")

        self.speak_thread = threading.Thread(target=self._speak_worker, daemon=True)
        self.speak_thread.start()

    def stop_reading(self):
        self.stop_requested = True
        if self.engine is not None:
            try:
                self.engine.stop()
            except Exception:
                pass
        self.status_var.set("Stopped.")
        self.run_btn.config(state="normal")
        self.upload_btn.config(state="normal")
        self.stop_btn.config(state="disabled")

    def _speak_worker(self):
        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", self.rate_var.get())

        used_word_events = {"fired": False}

        def on_word(name, location, length):
            used_word_events["fired"] = True
            if self.stop_requested:
                return
            self.ui_queue.put(("highlight_range", location, location + length))

        def on_end(name, completed):
            pass

        try:
            self.engine.connect("started-word", on_word)
        except Exception:
            pass  # driver doesn't support word events

        # Fallback: if no word events fire within the first couple seconds,
        # we highlight sentence-by-sentence instead by chunking speech calls.
        self.engine.connect("finished-utterance", on_end)

        if self._driver_supports_word_events():
            self.engine.say(self.full_text)
            self.engine.runAndWait()
        else:
            for start, end, sentence in self.sentences:
                if self.stop_requested:
                    break
                self.ui_queue.put(("highlight_range", start, end))
                self.engine.say(sentence)
                self.engine.runAndWait()

        self.ui_queue.put(("done", None, None))

    def _driver_supports_word_events(self):
        # SAPI5 (Windows) reliably supports per-word callbacks.
        import platform
        return platform.system() == "Windows"

    # ---------------- Cross-thread UI updates ----------------
    def _poll_queue(self):
        try:
            while True:
                msg, a, b = self.ui_queue.get_nowait()
                if msg == "highlight_range":
                    self._highlight_range(a, b)
                elif msg == "done":
                    self.status_var.set("Finished reading.")
                    self.run_btn.config(state="normal")
                    self.upload_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(50, self._poll_queue)

    def _highlight_range(self, start_idx, end_idx):
        self.text_widget.configure(state="normal")
        self.text_widget.tag_remove("highlight", "1.0", "end")
        start_pos = self._char_index_to_tk(start_idx)
        end_pos = self._char_index_to_tk(end_idx)
        self.text_widget.tag_add("highlight", start_pos, end_pos)
        self.text_widget.see(start_pos)
        self.text_widget.configure(state="disabled")

    def _char_index_to_tk(self, char_index):
        # Convert a plain character offset into a Tk "line.column" index.
        return f"1.0+{char_index}c"


def main():
    root = tk.Tk()
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    app = AudiobookReader(root)
    root.mainloop()


if __name__ == "__main__":
    main()
