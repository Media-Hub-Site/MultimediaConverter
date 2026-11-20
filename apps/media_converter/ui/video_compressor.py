import os
import sys
import subprocess
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QProgressBar, QMessageBox, QFrame, QComboBox,
    QDoubleSpinBox, QRadioButton, QButtonGroup
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

from apps.media_converter.ui.ui_theme import (
    get_app_colors,
    make_card_container,
    get_combo_style,
)
from apps.media_converter.converters.conversion import _find_ffmpeg


def _get_video_info(file_path: str, ffmpeg_path: str) -> dict:
    """Extrae duración (s), resolución y tamaño usando ffprobe o ffmpeg -i."""
    ffprobe_path = ffmpeg_path.replace("ffmpeg.exe", "ffprobe.exe").replace("ffmpeg", "ffprobe")
    duration = 0.0
    width = 0
    height = 0
    size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    if os.path.exists(ffprobe_path):
        try:
            cmd = [
                ffprobe_path, "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", file_path
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, startupinfo=startupinfo)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                duration = float(data.get("format", {}).get("duration", 0))
                size = float(data.get("format", {}).get("size", size))
                for stream in data.get("streams", []):
                    if stream.get("codec_type") == "video":
                        width = int(stream.get("width", 0))
                        height = int(stream.get("height", 0))
                        break
                if duration > 0:
                    return {"duration": duration, "size": size, "width": width, "height": height}
        except Exception:
            pass

    # Fallback con ffmpeg -i leyendo stderr
    try:
        import re
        cmd = [ffmpeg_path, "-i", file_path]
        res = subprocess.run(cmd, capture_output=True, text=True, startupinfo=startupinfo)
        m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", res.stderr)
        if m_dur:
            h, m, s = m_dur.groups()
            duration = int(h) * 3600 + int(m) * 60 + float(s)

        m_dim = re.search(r"Stream.*Video:.*,\s*(\d{2,5})x(\d{2,5})", res.stderr)
        if m_dim:
            width = int(m_dim.group(1))
            height = int(m_dim.group(2))
    except Exception:
        pass

    return {"duration": duration, "size": size, "width": width, "height": height}


class VideoCompressThread(QThread):
    progress = pyqtSignal(int, str)
    finished_success = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, input_path, output_path, mode, target_mb, crf_value, target_res="original"):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.mode = mode  # "target_size", "crf", or "percentage"
        self.target_mb = target_mb
        self.crf_value = crf_value
        self.target_res = target_res

    def run(self):
        try:
            ffmpeg_path = _find_ffmpeg()
            if not ffmpeg_path:
                self.error.emit("No se encontró FFmpeg en el sistema.")
                return

            self.progress.emit(10, "Analizando video...")
            info = _get_video_info(self.input_path, ffmpeg_path)
            duration = info.get("duration", 0)
            orig_size = info.get("size", 0)

            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            vf_filters = []
            if self.target_res == "720p":
                vf_filters.append("scale=-2:'min(720,ih)'")
            elif self.target_res == "480p":
                vf_filters.append("scale=-2:'min(480,ih)'")
            elif self.target_res == "360p":
                vf_filters.append("scale=-2:'min(360,ih)'")

            if self.mode == "target_size" and duration > 0:
                # Cálculo de bitrate objetivo: Total bits = target_MB * 8192 kbits
                total_kbits = self.target_mb * 8192
                target_total_bitrate = int(total_kbits / duration)
                audio_bitrate = min(128, max(48, int(target_total_bitrate * 0.15)))
                video_bitrate = max(target_total_bitrate - audio_bitrate, 64)

                self.progress.emit(30, "Comprimiendo video al tamaño seleccionado...")
                cmd = [
                    ffmpeg_path, "-y", "-i", self.input_path,
                    "-c:v", "libx264", "-b:v", f"{video_bitrate}k",
                    "-maxrate", f"{int(video_bitrate * 1.4)}k",
                    "-bufsize", f"{int(video_bitrate * 2)}k",
                    "-preset", "faster",
                ]
                if vf_filters:
                    cmd += ["-vf", ",".join(vf_filters)]
                cmd += [
                    "-c:a", "aac", "-b:a", f"{audio_bitrate}k",
                    self.output_path
                ]
            else:
                # Modo CRF (Calidad visual constante y reducción natural)
                self.progress.emit(30, "Optimizando video...")
                cmd = [
                    ffmpeg_path, "-y", "-i", self.input_path,
                    "-c:v", "libx264", "-crf", str(self.crf_value),
                    "-preset", "faster",
                ]
                if vf_filters:
                    cmd += ["-vf", ",".join(vf_filters)]
                cmd += [
                    "-c:a", "aac", "-b:a", "128k",
                    self.output_path
                ]

            self.progress.emit(50, "Procesando archivo...")
            proc = subprocess.run(cmd, startupinfo=startupinfo, capture_output=True, text=True)

            if proc.returncode != 0:
                self.error.emit(f"Error durante la compresión:\n{proc.stderr[-400:]}")
                return

            self.progress.emit(100, "¡Compresión completada!")
            self.finished_success.emit(self.output_path)

        except Exception as e:
            self.error.emit(str(e))


class VideoCompressorUI(QWidget):
    """Interfaz gráfica para compresión de video optimizada."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._colors = get_app_colors()
        self.file_path = None
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(28, 20, 28, 24)
        main_layout.setSpacing(16)

        card, card_layout = make_card_container()

        # Header
        header = QLabel("Compresor de Video")
        header.setStyleSheet(f"font-size: 19pt; font-weight: 800; color: {self._colors['text_main']};")
        desc = QLabel("Reduce el tamaño de tus videos para Discord, WhatsApp o Web manteniendo la mejor nitidez.")
        desc.setStyleSheet(f"color: {self._colors['text_muted']}; font-size: 10.5pt;")
        card_layout.addWidget(header)
        card_layout.addWidget(desc)
        card_layout.addSpacing(10)

        # Drop Zone
        self.drop_zone = QFrame()
        self.drop_zone.setAcceptDrops(True)
        self.drop_zone.setFixedHeight(140)
        self.drop_zone.setStyleSheet(f"""
            QFrame {{
                background-color: {self._colors['card']};
                border: 2px dashed {self._colors['border']};
                border-radius: 12px;
            }}
            QFrame:hover {{
                border-color: {self._colors['accent']};
                background-color: {self._colors['accent_soft']};
            }}
        """)
        drop_layout = QVBoxLayout(self.drop_zone)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.drop_label = QLabel("Arrastra tu video aquí o haz clic para seleccionar")
        self.drop_label.setStyleSheet(f"color: {self._colors['text_main']}; font-size: 11pt; font-weight: 600;")
        self.file_info_label = QLabel("Soporta MP4, MKV, MOV, AVI, WebM")
        self.file_info_label.setStyleSheet(f"color: {self._colors['text_muted']}; font-size: 9pt;")

        drop_layout.addWidget(self.drop_label, alignment=Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(self.file_info_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # Permitir clic en el DropZone
        self.drop_zone.mousePressEvent = lambda e: self.select_file()
        self.drop_zone.dragEnterEvent = self.drag_enter_event
        self.drop_zone.dropEvent = self.drop_event

        card_layout.addWidget(self.drop_zone)
        card_layout.addSpacing(8)

        # Opciones de Compresión
        opts_box = QFrame()
        opts_box.setStyleSheet(f"""
            QFrame {{
                background-color: {self._colors['card']};
                border: 1px solid {self._colors['border']};
                border-radius: 10px;
                padding: 12px;
            }}
        """)
        opts_layout = QVBoxLayout(opts_box)
        opts_layout.setContentsMargins(12, 10, 12, 10)
        opts_layout.setSpacing(12)

        lbl_opts = QLabel("Método de compresión:")
        lbl_opts.setStyleSheet(f"color: {self._colors['text_main']}; font-weight: 700; font-size: 10.5pt;")
        opts_layout.addWidget(lbl_opts)

        # Modo 1: Tamaño objetivo
        target_row = QHBoxLayout()
        self.radio_target = QRadioButton("Tamaño objetivo específico:")
        self.radio_target.setChecked(True)
        self.radio_target.setStyleSheet(f"color: {self._colors['text_main']}; font-weight: 600;")

        self.combo_preset = QComboBox()
        self.combo_preset.setStyleSheet(get_combo_style())
        self.combo_preset.addItems([
            "Discord (Máx 10 MB)",
            "WhatsApp (Máx 16 MB)",
            "Discord Nitro (Máx 25 MB)",
            "Correo (Máx 20 MB)",
            "Personalizado"
        ])
        self.combo_preset.currentIndexChanged.connect(self._on_preset_changed)

        self.spin_target_mb = QDoubleSpinBox()
        self.spin_target_mb.setRange(1.0, 500.0)
        self.spin_target_mb.setValue(10.0)
        self.spin_target_mb.setSuffix(" MB")
        self.spin_target_mb.setEnabled(False)
        self.spin_target_mb.setStyleSheet(f"""
            QDoubleSpinBox {{
                background-color: {self._colors['card']};
                color: {self._colors['text_main']};
                border: 1px solid {self._colors['border']};
                border-radius: 8px;
                padding: 4px 8px;
                font-weight: 600;
            }}
        """)

        target_row.addWidget(self.radio_target)
        target_row.addWidget(self.combo_preset)
        target_row.addWidget(self.spin_target_mb)
        target_row.addStretch()
        opts_layout.addLayout(target_row)

        # Modo 2: Nivel de compresión general
        crf_row = QHBoxLayout()
        self.radio_crf = QRadioButton("Nivel de compresión equilibrado:")
        self.radio_crf.setStyleSheet(f"color: {self._colors['text_main']}; font-weight: 600;")

        self.combo_crf = QComboBox()
        self.combo_crf.setStyleSheet(get_combo_style())
        self.combo_crf.addItems([
            "Ligera (Mejor calidad - CRF 22)",
            "Media (Recomendada - CRF 28)",
            "Agresiva (Máximo ahorro de espacio - CRF 32)",
            "Extrema (Máxima compresión - CRF 36)"
        ])
        self.combo_crf.setCurrentIndex(1)

        crf_row.addWidget(self.radio_crf)
        crf_row.addWidget(self.combo_crf)
        crf_row.addStretch()
        opts_layout.addLayout(crf_row)

        # Agrupar radio buttons de métodos
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.radio_target)
        self.mode_group.addButton(self.radio_crf)

        # Ajuste de resolución opcional
        res_row = QHBoxLayout()
        lbl_res = QLabel("Reducir resolución (opcional):")
        lbl_res.setStyleSheet(f"color: {self._colors['text_muted']}; font-weight: 600;")
        self.combo_res = QComboBox()
        self.combo_res.setStyleSheet(get_combo_style())
        self.combo_res.addItems([
            "Mantener resolución original",
            "Escalar a 720p HD (Máx 720p)",
            "Escalar a 480p SD (Ahorro notable)",
            "Escalar a 360p Móvil (Máximo ahorro)"
        ])
        res_row.addWidget(lbl_res)
        res_row.addWidget(self.combo_res)
        res_row.addStretch()
        opts_layout.addLayout(res_row)

        card_layout.addWidget(opts_box)
        card_layout.addSpacing(10)

        # Barra de progreso y botón de acción
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {self._colors['bg']};
                border-radius: 6px;
                border: 1px solid {self._colors['border']};
            }}
            QProgressBar::chunk {{
                background: {self._colors['accent']};
                border-radius: 5px;
            }}
        """)
        card_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(f"color: {self._colors['text_muted']}; font-weight: 600;")
        card_layout.addWidget(self.status_label)

        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()

        self.btn_compress = QPushButton("Iniciar Compresión")
        self.btn_compress.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_compress.setFixedHeight(46)
        self.btn_compress.setFixedWidth(220)
        self.btn_compress.setEnabled(False)
        self.btn_compress.setStyleSheet(f"""
            QPushButton {{
                background-color: {self._colors['accent']};
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                font-size: 11pt;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background-color: {self._colors['accent_hover']};
            }}
            QPushButton:disabled {{
                background-color: {self._colors['border']};
                color: {self._colors['text_muted']};
            }}
        """)
        self.btn_compress.clicked.connect(self.start_compression)
        bottom_layout.addWidget(self.btn_compress)
        bottom_layout.addStretch()

        card_layout.addLayout(bottom_layout)
        main_layout.addWidget(card)

    def _on_preset_changed(self, idx):
        presets = {0: 10.0, 1: 16.0, 2: 25.0, 3: 20.0}
        if idx in presets:
            self.spin_target_mb.setValue(presets[idx])
            self.spin_target_mb.setEnabled(False)
        else:
            self.spin_target_mb.setEnabled(True)

    def drag_enter_event(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(urls[0].toLocalFile().lower().endswith(ext) for ext in [".mp4", ".mkv", ".mov", ".avi", ".webm"]):
                event.acceptProposedAction()

    def drop_event(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self._set_file(path)

    def select_file(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar Video", "", "Videos (*.mp4 *.mkv *.mov *.avi *.webm)"
        )
        if file:
            self._set_file(file)

    def _set_file(self, file_path):
        self.file_path = file_path
        name = os.path.basename(file_path)
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        self.drop_label.setText(name)
        self.file_info_label.setText(f"Tamaño original: {size_mb:.2f} MB")
        self.btn_compress.setEnabled(True)
        self.status_label.setText("")

    def start_compression(self):
        if not self.file_path or not os.path.exists(self.file_path):
            return

        base, ext = os.path.splitext(self.file_path)
        output_path, _ = QFileDialog.getSaveFileName(
            self, "Guardar Video Comprimido", f"{base}_comprimido.mp4", "Video MP4 (*.mp4)"
        )
        if not output_path:
            return

        mode = "target_size" if self.radio_target.isChecked() else "crf"
        target_mb = self.spin_target_mb.value()
        crf_map = {0: 22, 1: 28, 2: 32, 3: 36}
        crf_val = crf_map.get(self.combo_crf.currentIndex(), 28)
        
        res_map = {0: "original", 1: "720p", 2: "480p", 3: "360p"}
        target_res = res_map.get(self.combo_res.currentIndex(), "original")

        self.btn_compress.setEnabled(False)
        self.drop_zone.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(10)

        self.worker = VideoCompressThread(
            self.file_path, output_path, mode, target_mb, crf_val, target_res
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_success.connect(self._on_success)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, val, msg):
        self.progress_bar.setValue(val)
        self.status_label.setText(msg)

    def _on_success(self, output_path):
        self.btn_compress.setEnabled(True)
        self.drop_zone.setEnabled(True)
        self.progress_bar.setVisible(False)
        orig_mb = os.path.getsize(self.file_path) / (1024 * 1024)
        new_mb = os.path.getsize(output_path) / (1024 * 1024)
        saved = ((orig_mb - new_mb) / orig_mb) * 100 if orig_mb > 0 else 0
        self.status_label.setText(f"Guardado: {new_mb:.2f} MB (Ahorro del {saved:.1f}%)")
        QMessageBox.information(
            self, "Compresión Exitosa",
            f"El video se comprimió correctamente:\n\n"
            f"• Original: {orig_mb:.2f} MB\n"
            f"• Comprimido: {new_mb:.2f} MB ({saved:.1f}% menor)\n\n"
            f"Ruta: {output_path}"
        )

    def _on_error(self, err):
        self.btn_compress.setEnabled(True)
        self.drop_zone.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Error al comprimir")
        QMessageBox.critical(self, "Error", f"Ocurrió un error al procesar el video:\n{err}")
