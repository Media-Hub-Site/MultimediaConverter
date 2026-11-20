import os
import sys
import subprocess
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QProgressBar, QMessageBox, QFrame, QDoubleSpinBox,
    QCheckBox, QSlider, QStackedLayout
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

from apps.media_converter.ui.ui_theme import (
    get_app_colors,
    make_card_container,
)
from apps.media_converter.converters.conversion import _find_ffmpeg
from apps.media_converter.ui.video_compressor import _get_video_info


def format_time_minutes(seconds: float) -> str:
    """Formatea segundos en formato min:seg (ej: 01:25.4)."""
    mins = int(seconds // 60)
    secs = seconds % 60
    return f"{mins:02d}:{secs:04.1f}"


class MediaCutThread(QThread):
    progress = pyqtSignal(int, str)
    finished_success = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, input_path, output_path, start_sec, end_sec, stream_copy):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.start_sec = start_sec
        self.end_sec = end_sec
        self.stream_copy = stream_copy

    def run(self):
        try:
            ffmpeg_path = _find_ffmpeg()
            if not ffmpeg_path:
                self.error.emit("No se encontró FFmpeg.")
                return

            self.progress.emit(20, "Preparando corte...")
            duration = max(self.end_sec - self.start_sec, 0.1)

            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            if self.stream_copy:
                cmd = [
                    ffmpeg_path, "-y",
                    "-ss", str(self.start_sec),
                    "-t", str(duration),
                    "-i", self.input_path,
                    "-c", "copy",
                    self.output_path
                ]
            else:
                cmd = [
                    ffmpeg_path, "-y",
                    "-ss", str(self.start_sec),
                    "-t", str(duration),
                    "-i", self.input_path,
                    self.output_path
                ]

            self.progress.emit(60, "Recortando archivo multimedia...")
            proc = subprocess.run(cmd, startupinfo=startupinfo, capture_output=True, text=True)

            if proc.returncode != 0:
                cmd_fallback = [
                    ffmpeg_path, "-y",
                    "-ss", str(self.start_sec),
                    "-t", str(duration),
                    "-i", self.input_path,
                    self.output_path
                ]
                proc2 = subprocess.run(cmd_fallback, startupinfo=startupinfo, capture_output=True, text=True)
                if proc2.returncode != 0:
                    self.error.emit(f"Error al cortar:\n{proc2.stderr[-300:]}")
                    return

            self.progress.emit(100, "¡Corte completado!")
            self.finished_success.emit(self.output_path)

        except Exception as e:
            self.error.emit(str(e))


class MediaCutterUI(QWidget):
    """Interfaz gráfica unificada para recortar video y audio con visor en tiempo real y timeline en minutos."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._colors = get_app_colors()
        self.file_path = None
        self.duration_seconds = 0.0
        self.is_video = False

        # Configuración de QtMediaPlayer
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.8)

        self.init_ui()

        self.player.positionChanged.connect(self._on_player_position_changed)
        self.player.durationChanged.connect(self._on_player_duration_changed)

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(28, 16, 28, 20)
        main_layout.setSpacing(12)

        card, card_layout = make_card_container()

        header = QLabel("Cortador Multimedia con Visor")
        header.setStyleSheet(f"font-size: 18pt; font-weight: 800; color: {self._colors['text_main']};")
        desc = QLabel("Visualiza y escucha tu archivo en tiempo real, ajusta los puntos de inicio y fin en minutos y recorta con precisión.")
        desc.setStyleSheet(f"color: {self._colors['text_muted']}; font-size: 10pt;")
        card_layout.addWidget(header)
        card_layout.addWidget(desc)
        card_layout.addSpacing(4)

        # Drop Zone inicial
        self.drop_zone = QFrame()
        self.drop_zone.setAcceptDrops(True)
        self.drop_zone.setFixedHeight(85)
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
        drop_layout.setContentsMargins(10, 8, 10, 8)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.drop_label = QLabel("Arrastra un video o audio aquí o haz clic para seleccionarlo")
        self.drop_label.setStyleSheet(f"color: {self._colors['text_main']}; font-size: 10.5pt; font-weight: 600;")
        self.file_info_label = QLabel("Formatos compatibles: MP4, MKV, MOV, AVI, WebM, MP3, WAV, AAC, FLAC, OGG...")
        self.file_info_label.setStyleSheet(f"color: {self._colors['text_muted']}; font-size: 8.5pt;")

        drop_layout.addWidget(self.drop_label, alignment=Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(self.file_info_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self.drop_zone.mousePressEvent = lambda e: self.select_file()
        self.drop_zone.dragEnterEvent = self.drag_enter_event
        self.drop_zone.dropEvent = self.drop_event

        card_layout.addWidget(self.drop_zone)

        # ── Visor multimedia en tiempo real ──
        self.viewer_frame = QFrame()
        self.viewer_frame.setFixedHeight(230)
        self.viewer_frame.setStyleSheet(f"""
            QFrame {{
                background-color: #0b0c10;
                border: 1px solid {self._colors['border']};
                border-radius: 10px;
            }}
        """)
        viewer_layout = QVBoxLayout(self.viewer_frame)
        viewer_layout.setContentsMargins(0, 0, 0, 0)

        # Video Widget
        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background-color: #0b0c10; border-radius: 10px;")
        self.player.setVideoOutput(self.video_widget)

        # Placeholder para Audio
        self.audio_placeholder = QLabel("🎵 Reproduciendo pista de audio")
        self.audio_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.audio_placeholder.setStyleSheet("color: #E0E0E0; font-size: 13pt; font-weight: 700; background: transparent;")
        self.audio_placeholder.hide()

        viewer_layout.addWidget(self.video_widget)
        viewer_layout.addWidget(self.audio_placeholder)

        card_layout.addWidget(self.viewer_frame)

        # ── Controles de Reproducción y Barra Ajustable de Timeline ──
        timeline_box = QFrame()
        timeline_box.setStyleSheet(f"""
            QFrame {{
                background-color: {self._colors['card']};
                border: 1px solid {self._colors['border']};
                border-radius: 10px;
                padding: 6px 12px;
            }}
        """)
        timeline_layout = QVBoxLayout(timeline_box)
        timeline_layout.setContentsMargins(10, 6, 10, 6)
        timeline_layout.setSpacing(6)

        # Fila superior de timeline: Botones Play/Pausa, tiempo transcurrido y duración
        play_row = QHBoxLayout()
        self.btn_play = QPushButton("▶ Reproducir")
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setFixedHeight(30)
        self.btn_play.setFixedWidth(115)
        self.btn_play.clicked.connect(self._toggle_playback)
        self.btn_play.setStyleSheet(f"""
            QPushButton {{
                background-color: {self._colors['accent']};
                color: #FFFFFF;
                border-radius: 6px;
                font-weight: 700;
                border: none;
            }}
            QPushButton:hover {{
                background-color: {self._colors['accent_hover']};
            }}
        """)

        self.btn_preview_cut = QPushButton("✂ Vista previa del corte")
        self.btn_preview_cut.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_preview_cut.setFixedHeight(30)
        self.btn_preview_cut.setFixedWidth(170)
        self.btn_preview_cut.clicked.connect(self._preview_cut)
        self.btn_preview_cut.setStyleSheet(f"""
            QPushButton {{
                background-color: {self._colors['accent_soft']};
                color: {self._colors['text_main']};
                border: 1px solid {self._colors['border']};
                border-radius: 6px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {self._colors['accent']};
                color: #FFFFFF;
            }}
        """)

        self.lbl_time_display = QLabel("00:00.0 / 00:00.0 (min:seg)")
        self.lbl_time_display.setStyleSheet(f"color: {self._colors['text_main']}; font-weight: 700; font-size: 10pt;")

        play_row.addWidget(self.btn_play)
        play_row.addWidget(self.btn_preview_cut)
        play_row.addStretch()
        play_row.addWidget(self.lbl_time_display)
        timeline_layout.addLayout(play_row)

        # Barra Slider ajustable con medidas
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setRange(0, 1000)
        self.time_slider.setValue(0)
        self.time_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.time_slider.sliderMoved.connect(self._on_slider_moved)
        self.time_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background: {self._colors['border']};
                height: 8px;
                border-radius: 4px;
            }}
            QSlider::sub-page:horizontal {{
                background: {self._colors['accent']};
                border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: #FFFFFF;
                border: 2px solid {self._colors['accent']};
                width: 18px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 9px;
            }}
        """)
        timeline_layout.addWidget(self.time_slider)
        card_layout.addWidget(timeline_box)

        # ── Puntos de Corte interactivos (en minutos y segundos) ──
        cut_box = QFrame()
        cut_box.setStyleSheet(f"""
            QFrame {{
                background-color: {self._colors['card']};
                border: 1px solid {self._colors['border']};
                border-radius: 10px;
                padding: 8px 12px;
            }}
        """)
        cut_layout = QVBoxLayout(cut_box)
        cut_layout.setContentsMargins(10, 8, 10, 8)
        cut_layout.setSpacing(8)

        row_time = QHBoxLayout()

        lbl_start = QLabel("Punto de inicio:")
        lbl_start.setStyleSheet(f"color: {self._colors['text_main']}; font-weight: 700; font-size: 10pt;")
        self.spin_start = QDoubleSpinBox()
        self.spin_start.setRange(0.0, 999999.0)
        self.spin_start.setValue(0.0)
        self.spin_start.setSuffix(" s")
        self._style_spin(self.spin_start)
        self.spin_start.valueChanged.connect(self._update_cut_labels)

        self.lbl_start_min = QLabel("(00:00.0 min)")
        self.lbl_start_min.setStyleSheet(f"color: {self._colors['text_muted']}; font-weight: 600;")

        btn_set_start = QPushButton("Fijar actual")
        btn_set_start.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_set_start.setStyleSheet(f"""
            QPushButton {{
                background: {self._colors['accent_soft']};
                color: {self._colors['text_main']};
                border: 1px solid {self._colors['border']};
                border-radius: 6px;
                padding: 4px 8px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {self._colors['accent']};
                color: #FFFFFF;
            }}
        """)
        btn_set_start.clicked.connect(self._set_start_from_current)

        row_time.addWidget(lbl_start)
        row_time.addWidget(self.spin_start)
        row_time.addWidget(self.lbl_start_min)
        row_time.addWidget(btn_set_start)
        row_time.addSpacing(20)

        lbl_end = QLabel("Punto final:")
        lbl_end.setStyleSheet(f"color: {self._colors['text_main']}; font-weight: 700; font-size: 10pt;")
        self.spin_end = QDoubleSpinBox()
        self.spin_end.setRange(0.1, 999999.0)
        self.spin_end.setValue(30.0)
        self.spin_end.setSuffix(" s")
        self._style_spin(self.spin_end)
        self.spin_end.valueChanged.connect(self._update_cut_labels)

        self.lbl_end_min = QLabel("(00:30.0 min)")
        self.lbl_end_min.setStyleSheet(f"color: {self._colors['text_muted']}; font-weight: 600;")

        btn_set_end = QPushButton("Fijar actual")
        btn_set_end.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_set_end.setStyleSheet(f"""
            QPushButton {{
                background: {self._colors['accent_soft']};
                color: {self._colors['text_main']};
                border: 1px solid {self._colors['border']};
                border-radius: 6px;
                padding: 4px 8px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {self._colors['accent']};
                color: #FFFFFF;
            }}
        """)
        btn_set_end.clicked.connect(self._set_end_from_current)

        row_time.addWidget(lbl_end)
        row_time.addWidget(self.spin_end)
        row_time.addWidget(self.lbl_end_min)
        row_time.addWidget(btn_set_end)
        row_time.addStretch()
        cut_layout.addLayout(row_time)

        self.chk_lossless = QCheckBox("Modo Ultra Rápido sin pérdida (Stream Copy directo)")
        self.chk_lossless.setChecked(True)
        self.chk_lossless.setStyleSheet(f"color: {self._colors['text_muted']}; font-weight: 600; font-size: 9.5pt;")
        cut_layout.addWidget(self.chk_lossless)

        card_layout.addWidget(cut_box)

        # Barra de progreso
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(10)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {self._colors['card']};
                border-radius: 5px;
                border: 1px solid {self._colors['border']};
            }}
            QProgressBar::chunk {{
                background: {self._colors['accent']};
                border-radius: 4px;
            }}
        """)
        card_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(f"color: {self._colors['text_muted']}; font-weight: 600;")
        card_layout.addWidget(self.status_label)

        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()

        self.btn_cut = QPushButton("Recortar Archivo")
        self.btn_cut.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cut.setFixedHeight(42)
        self.btn_cut.setFixedWidth(220)
        self.btn_cut.setEnabled(False)
        self.btn_cut.setStyleSheet(f"""
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
        self.btn_cut.clicked.connect(self.start_cut)
        bottom_layout.addWidget(self.btn_cut)
        bottom_layout.addStretch()

        card_layout.addLayout(bottom_layout)
        main_layout.addWidget(card)

    def _style_spin(self, spin):
        spin.setStyleSheet(f"""
            QDoubleSpinBox {{
                background-color: {self._colors['card']};
                color: {self._colors['text_main']};
                border: 1px solid {self._colors['border']};
                border-radius: 8px;
                padding: 4px 8px;
                font-weight: 600;
                min-width: 90px;
            }}
        """)

    def _toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("▶ Reproducir")
        else:
            self.player.play()
            self.btn_play.setText("⏸ Pausar")

    def _preview_cut(self):
        """Reproduce exactamente desde el punto de inicio fijado."""
        start_ms = int(self.spin_start.value() * 1000)
        self.player.setPosition(start_ms)
        self.player.play()
        self.btn_play.setText("⏸ Pausar")

    def _set_start_from_current(self):
        cur_sec = self.player.position() / 1000.0
        self.spin_start.setValue(cur_sec)

    def _set_end_from_current(self):
        cur_sec = self.player.position() / 1000.0
        self.spin_end.setValue(cur_sec)

    def _update_cut_labels(self):
        s_val = self.spin_start.value()
        e_val = self.spin_end.value()
        self.lbl_start_min.setText(f"({format_time_minutes(s_val)} min)")
        self.lbl_end_min.setText(f"({format_time_minutes(e_val)} min)")

    def _on_player_position_changed(self, position_ms):
        pos_sec = position_ms / 1000.0
        dur_sec = self.duration_seconds
        self.lbl_time_display.setText(
            f"{format_time_minutes(pos_sec)} / {format_time_minutes(dur_sec)} (min:seg)"
        )
        if dur_sec > 0:
            val = int((pos_sec / dur_sec) * 1000)
            self.time_slider.blockSignals(True)
            self.time_slider.setValue(val)
            self.time_slider.blockSignals(False)

        # Si estamos en previsualización de corte y alcanzamos el final
        if pos_sec >= self.spin_end.value() and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("▶ Reproducir")

    def _on_player_duration_changed(self, duration_ms):
        if duration_ms > 0:
            self.duration_seconds = duration_ms / 1000.0
            self.spin_start.setRange(0.0, self.duration_seconds)
            self.spin_end.setRange(0.1, self.duration_seconds)
            if self.spin_end.value() > self.duration_seconds or self.spin_end.value() == 30.0:
                self.spin_end.setValue(self.duration_seconds)
            self._update_cut_labels()

    def _on_slider_moved(self, value):
        if self.duration_seconds > 0:
            target_ms = int((value / 1000.0) * self.duration_seconds * 1000)
            self.player.setPosition(target_ms)

    def drag_enter_event(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def drop_event(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self._set_file(path)

    def select_file(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar Archivo Multimedia", "", "Multimedia (*.mp4 *.mkv *.mov *.avi *.webm *.mp3 *.wav *.flac *.aac *.ogg *.m4a)"
        )
        if file:
            self._set_file(file)

    def _set_file(self, file_path):
        self.file_path = file_path
        name = os.path.basename(file_path)
        ext = os.path.splitext(file_path)[1].lower()
        self.is_video = ext in [".mp4", ".mkv", ".mov", ".avi", ".webm"]

        ffmpeg_path = _find_ffmpeg()
        info = _get_video_info(file_path, ffmpeg_path) if ffmpeg_path else {"duration": 0}
        self.duration_seconds = info.get("duration", 0)

        self.drop_label.setText(name)
        dur_text = f"Duración: {format_time_minutes(self.duration_seconds)} min" if self.duration_seconds > 0 else ""
        self.file_info_label.setText(f"{'Video' if self.is_video else 'Audio'} listo para recortar • {dur_text}")
        self.btn_cut.setEnabled(True)
        self.status_label.setText("")

        # Configurar reproducción en visor
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(file_path))

        if self.is_video:
            self.video_widget.show()
            self.audio_placeholder.hide()
        else:
            self.video_widget.hide()
            self.audio_placeholder.setText(f"🎵 Audio: {name}")
            self.audio_placeholder.show()

        if self.duration_seconds > 0:
            self.spin_start.setRange(0.0, self.duration_seconds)
            self.spin_end.setRange(0.1, self.duration_seconds)
            self.spin_end.setValue(min(30.0, self.duration_seconds))
            self._update_cut_labels()

        self.btn_play.setText("▶ Reproducir")

    def start_cut(self):
        if not self.file_path or not os.path.exists(self.file_path):
            return

        self.player.pause()
        self.btn_play.setText("▶ Reproducir")

        start_val = self.spin_start.value()
        end_val = self.spin_end.value()
        if start_val >= end_val:
            QMessageBox.warning(self, "Rango Inválido", "El punto de inicio debe ser menor que el punto final.")
            return

        base, ext = os.path.splitext(self.file_path)
        output_path, _ = QFileDialog.getSaveFileName(
            self, "Guardar Archivo Recortado", f"{base}_recorte{ext}", f"Archivo (*{ext})"
        )
        if not output_path:
            return

        self.btn_cut.setEnabled(False)
        self.drop_zone.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(15)

        stream_copy = self.chk_lossless.isChecked()
        self.worker = MediaCutThread(
            self.file_path, output_path, start_val, end_val, stream_copy
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_success.connect(self._on_success)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, val, msg):
        self.progress_bar.setValue(val)
        self.status_label.setText(msg)

    def _on_success(self, output_path):
        self.btn_cut.setEnabled(True)
        self.drop_zone.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Archivo recortado guardado con éxito")
        QMessageBox.information(
            self, "Recorte Completado",
            f"El fragmento se ha recortado correctamente:\n\n{output_path}"
        )

    def _on_error(self, err):
        self.btn_cut.setEnabled(True)
        self.drop_zone.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Error al recortar")
        QMessageBox.critical(self, "Error", f"Ocurrió un error al recortar el archivo:\n{err}")

    def closeEvent(self, event):
        self.player.stop()
        super().closeEvent(event)
