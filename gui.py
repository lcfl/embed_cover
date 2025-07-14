# pyinstaller --onefile --windowed --add-data "ffmpeg.exe;." --add-data "ffprobe.exe;." --icon="icon.ico" gui.py
import sys
import os
import subprocess
import json
import shutil
import tempfile
import multiprocessing
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QFileDialog,
    QTextEdit, QVBoxLayout, QHBoxLayout, QListWidget,
    QLineEdit, QCheckBox, QMessageBox, QGroupBox, QGridLayout,
    QProgressBar, QRadioButton, QButtonGroup, QSpinBox, QDoubleSpinBox,
    QListWidgetItem, QSplitter, QTabWidget, QFrame, QScrollArea
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl, QRunnable, QThreadPool, QObject
from PyQt5.QtGui import QDragEnterEvent, QDropEvent

# --- 全局配置 ---
CONFIG_FILE = 'config.json'
LOG_FILE = 'log.txt'

DEFAULT_CONFIG = {
    "extensions": [".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".wmv"],
    "custom_extensions": [],
    "use_percent": True,
    "percent_value": 20.0,
    "time_seconds": 20,
    "save_to_new_dir": False,
    "output_dir": "",
    "overwrite_existing": False, 
    "max_workers": multiprocessing.cpu_count()
}

# --- 并行工作单元 ---
class WorkerSignals(QObject):
    """
    为工作线程定义信号，用于与主线程通信。
    """
    finished = pyqtSignal()
    error = pyqtSignal(str)
    result = pyqtSignal(str)
    progress = pyqtSignal(str, str) # filename, status

class Worker(QRunnable):
    """
    工作线程，继承自 QRunnable，用于在线程池中执行。
    每个 Worker 实例处理一个视频文件。
    """
    def __init__(self, video_path, config):
        super().__init__()
        self.video_path = video_path
        self.config = config
        self.signals = WorkerSignals()

    def run(self):
        """线程执行的入口点"""
        try:
            filename = os.path.basename(self.video_path)
            mode = 'percent' if self.config['use_percent'] else 'time'
            value = self.config['percent_value'] if mode == 'percent' else self.config['time_seconds']
            out_dir = self.config['output_dir'] if self.config['save_to_new_dir'] else None
            
            status = self.process_single_video(self.video_path, mode, value, out_dir)
            self.signals.progress.emit(filename, status)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()

    def process_single_video(self, video_path, mode, value, out_dir):
        """处理单个视频文件的核心逻辑"""
        if not self.config.get("overwrite_existing", False):
            if self.has_embedded_cover(video_path):
                return "已包含封面，自动跳过"
        
        duration = self.get_video_duration(video_path)
        if duration == 0:
            return "错误：无法获取视频时长"
        
        if mode == 'time' and duration < value:
            return f"警告：视频时长 ({duration:.1f}s) 小于设定的截图时间 ({value}s)，跳过"
        
        second = (duration * value / 100) if mode == 'percent' else value
        temp_cover = ""
        temp_output = ""
        
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf_cover, \
                 tempfile.NamedTemporaryFile(suffix=os.path.splitext(video_path)[1], delete=False) as tf_output:
                temp_cover = tf_cover.name
                temp_output = tf_output.name

            ffmpeg_extract_cmd = [
                'ffmpeg', '-ss', str(second), '-i', video_path,
                '-vframes', '1', '-q:v', '2', '-y', temp_cover
            ]
            subprocess.run(ffmpeg_extract_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if not os.path.exists(temp_cover) or os.path.getsize(temp_cover) == 0:
                return "错误：提取封面失败"

            ffmpeg_embed_cmd = [
                'ffmpeg', '-i', video_path, '-i', temp_cover,
                '-map', '0', '-map', '1', '-c', 'copy', 
                '-disposition:v:1', 'attached_pic', '-y', temp_output
            ]
            subprocess.run(ffmpeg_embed_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if out_dir:
                final_path = os.path.join(out_dir, os.path.basename(video_path))
                shutil.move(temp_output, final_path)
            else:
                shutil.move(temp_output, video_path)
            
            return "成功嵌入封面"
            
        except subprocess.CalledProcessError as e:
            return f"错误：FFmpeg 处理失败 - {e}"
        except Exception as e:
            return f"错误：发生未知异常 - {str(e)}"
        finally:
            for f in [temp_cover, temp_output]:
                if f and os.path.exists(f):
                    try:
                        os.remove(f)
                    except OSError:
                        pass

    def has_embedded_cover(self, video_path):
        try:
            result = subprocess.run([
                'ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', video_path
            ], capture_output=True, text=True, check=True, encoding='utf-8')
            data = json.loads(result.stdout)
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video' and stream.get('disposition', {}).get('attached_pic', 0) == 1:
                    return True
        except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
            return False
        return False

    def get_video_duration(self, video_path):
        try:
            result = subprocess.run([
                'ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', video_path
            ], capture_output=True, text=True, check=True, encoding='utf-8')
            data = json.loads(result.stdout)
            return float(data.get('format', {}).get('duration', 0))
        except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
            return 0
        return 0

# --- 自定义UI组件 ---
class DragDropArea(QFrame):
    files_dropped = pyqtSignal(list)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.normal_style = "QFrame { border: 2px dashed #aaa; border-radius: 10px; background-color: #f9f9f9; }"
        self.hover_style = "QFrame { border: 2px dashed #0078d4; border-radius: 10px; background-color: #e6f3ff; }"
        self.setStyleSheet(self.normal_style)
        
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        
        icon_label = QLabel("📁", self)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet("font-size: 48px; color: #666; border: none; background: transparent;")
        
        text_label = QLabel("拖拽视频文件或文件夹到此处\n或点击下方按钮选择", self)
        text_label.setAlignment(Qt.AlignCenter)
        text_label.setStyleSheet("font-size: 14px; color: #666; border: none; background: transparent;")
        
        layout.addWidget(icon_label)
        layout.addWidget(text_label)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self.hover_style)

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self.normal_style)

    def dropEvent(self, event: QDropEvent):
        urls = [url.toLocalFile() for url in event.mimeData().urls()]
        self.files_dropped.emit(urls)
        self.setStyleSheet(self.normal_style)

class CustomFormatWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        input_layout = QHBoxLayout()
        self.format_input = QLineEdit(placeholderText="例如: .ts")
        self.add_btn = QPushButton("添加")
        input_layout.addWidget(self.format_input)
        input_layout.addWidget(self.add_btn)
        layout.addLayout(input_layout)
        
        self.format_list = QListWidget()
        self.format_list.setMaximumHeight(80)
        layout.addWidget(self.format_list)
        
        self.remove_btn = QPushButton("删除选中")
        self.remove_btn.setStyleSheet("background-color: #ffc107; color: #212529;")
        layout.addWidget(self.remove_btn, alignment=Qt.AlignRight)
        
        self.add_btn.clicked.connect(self.add_format)
        self.remove_btn.clicked.connect(self.remove_format)
        self.format_input.returnPressed.connect(self.add_format)
        
    def add_format(self):
        text = self.format_input.text().strip().lower()
        if not text: return
        if not text.startswith('.'): text = '.' + text
        
        if not self.format_list.findItems(text, Qt.MatchExactly):
            self.format_list.addItem(text)
            self.format_input.clear()
        else:
            QMessageBox.warning(self, "重复", f"格式 '{text}' 已存在。")
    
    def remove_format(self):
        for item in self.format_list.selectedItems():
            self.format_list.takeItem(self.format_list.row(item))
    
    def get_formats(self):
        return [self.format_list.item(i).text() for i in range(self.format_list.count())]
    
    def set_formats(self, formats):
        self.format_list.clear()
        self.format_list.addItems(formats)

# --- 主应用窗口 ---
class MainApp(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = self.load_config()
        self.files = set()
        self.threadpool = QThreadPool()
        self.active_workers = 0
        self.init_ui()
        self.load_settings_to_ui()
        
    def init_ui(self):
        self.setWindowTitle("🎬 视频封面嵌入工具")
        self.resize(950, 768)
        self.setMinimumSize(800, 600)
        self.set_stylesheet()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        title_label = QLabel("视频封面嵌入工具", self)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setObjectName("TitleLabel")
        main_layout.addWidget(title_label)
        
        tab_widget = QTabWidget(self)
        main_tab = self.create_main_tab()
        settings_tab = self.create_settings_tab()
        tab_widget.addTab(main_tab, "📽️ 主要功能")
        tab_widget.addTab(settings_tab, "⚙️ 高级设置")
        main_layout.addWidget(tab_widget)
        
        self.connect_signals()

    def create_main_tab(self):
        main_tab_container = QWidget()
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setWidget(main_tab_container)
        main_layout = QVBoxLayout(main_tab_container)

        top_splitter = QSplitter(Qt.Horizontal)
        
        file_group = QGroupBox("📁 1. 添加文件")
        file_layout = QVBoxLayout(file_group)
        self.drag_area = DragDropArea()
        file_layout.addWidget(self.drag_area)
        
        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(150)
        file_layout.addWidget(QLabel("文件列表:"))
        file_layout.addWidget(self.file_list)
        
        file_btn_layout = QHBoxLayout()
        self.add_files_btn = QPushButton("添加文件")
        self.add_folder_btn = QPushButton("添加文件夹")
        self.clear_files_btn = QPushButton("清空列表")
        self.remove_selected_btn = QPushButton("移除选中")
        self.clear_files_btn.setObjectName("DangerButton")
        self.remove_selected_btn.setObjectName("WarningButton")
        file_btn_layout.addWidget(self.add_files_btn)
        file_btn_layout.addWidget(self.add_folder_btn)
        file_btn_layout.addStretch()
        file_btn_layout.addWidget(self.remove_selected_btn)
        file_btn_layout.addWidget(self.clear_files_btn)
        file_layout.addLayout(file_btn_layout)
        top_splitter.addWidget(file_group)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0,0,0,0)
        
        format_group = QGroupBox("🎭 2. 选择格式")
        format_layout = QVBoxLayout(format_group)
        format_grid = QGridLayout()
        self.format_checks = {}
        common_formats = ['.mp4', '.mkv', '.mov', '.avi', '.webm', '.flv', '.m4v', '.wmv']
        for i, ext in enumerate(common_formats):
            chk = QCheckBox(ext)
            self.format_checks[ext] = chk
            format_grid.addWidget(chk, i // 4, i % 4)
        format_layout.addLayout(format_grid)
        format_layout.addWidget(QLabel("自定义格式:"))
        self.custom_format_widget = CustomFormatWidget()
        format_layout.addWidget(self.custom_format_widget)
        right_layout.addWidget(format_group)
        
        settings_grid = QGridLayout()
        screenshot_group = QGroupBox("📸 3. 截图设置")
        screenshot_layout = QVBoxLayout(screenshot_group)
        self.mode_group = QButtonGroup()
        self.percent_radio = QRadioButton("按视频百分比")
        self.time_radio = QRadioButton("按固定时间")
        self.mode_group.addButton(self.percent_radio)
        self.mode_group.addButton(self.time_radio)
        self.percent_spin = QDoubleSpinBox(suffix="%", decimals=1, singleStep=5)
        self.percent_spin.setRange(0.1, 99.9)
        self.time_spin = QSpinBox(suffix=" 秒", singleStep=10)
        self.time_spin.setRange(1, 7200)
        ss_grid = QGridLayout()
        ss_grid.addWidget(self.percent_radio, 0, 0)
        ss_grid.addWidget(self.percent_spin, 0, 1)
        ss_grid.addWidget(self.time_radio, 1, 0)
        ss_grid.addWidget(self.time_spin, 1, 1)
        screenshot_layout.addLayout(ss_grid)
        
        output_group = QGroupBox("💾 4. 输出设置")
        output_layout = QVBoxLayout(output_group)
        self.save_new_checkbox = QCheckBox("保存到新目录 (否则覆盖原文件)")
        output_dir_layout = QHBoxLayout()
        self.output_dir_input = QLineEdit(placeholderText="选择输出目录...")
        self.browse_output_btn = QPushButton("浏览")
        output_dir_layout.addWidget(self.output_dir_input)
        output_dir_layout.addWidget(self.browse_output_btn)
        output_layout.addWidget(self.save_new_checkbox)
        output_layout.addLayout(output_dir_layout)

        settings_grid.addWidget(screenshot_group, 0, 0)
        settings_grid.addWidget(output_group, 0, 1)
        right_layout.addLayout(settings_grid)
        top_splitter.addWidget(right_panel)
        
        top_splitter.setStretchFactor(0, 5)
        top_splitter.setStretchFactor(1, 4)
        main_layout.addWidget(top_splitter)

        bottom_splitter = QSplitter(Qt.Vertical)
        
        control_group = QGroupBox("🚀 5. 开始处理")
        control_layout = QVBoxLayout(control_group)
        self.progress_bar = QProgressBar(visible=False, textVisible=True, format="%v / %m")
        control_layout.addWidget(self.progress_bar)
        control_btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("开始处理")
        self.stop_btn = QPushButton("停止处理", enabled=False)
        self.start_btn.setObjectName("SuccessButton")
        self.stop_btn.setObjectName("DangerButton")
        control_btn_layout.addWidget(self.start_btn)
        control_btn_layout.addWidget(self.stop_btn)
        control_btn_layout.addStretch()
        control_layout.addLayout(control_btn_layout)
        bottom_splitter.addWidget(control_group)

        log_group = QGroupBox("📋 处理日志")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QTextEdit(readOnly=True)
        log_btn_layout = QHBoxLayout()
        self.clear_log_btn = QPushButton("清空日志")
        self.save_log_btn = QPushButton("保存日志")
        log_btn_layout.addStretch()
        log_btn_layout.addWidget(self.clear_log_btn)
        log_btn_layout.addWidget(self.save_log_btn)
        log_layout.addWidget(self.log_view)
        log_layout.addLayout(log_btn_layout)
        bottom_splitter.addWidget(log_group)
        
        main_layout.addWidget(bottom_splitter)
        
        return scroll_area

    def create_settings_tab(self):
        settings_tab = QWidget()
        layout = QVBoxLayout(settings_tab)
        
        behavior_group = QGroupBox("处理行为设置")
        behavior_layout = QVBoxLayout(behavior_group)
        self.overwrite_checkbox = QCheckBox("覆盖已有封面")
        self.overwrite_checkbox.setToolTip("如果选中，即使视频已存在封面，也会重新生成并覆盖。")
        behavior_layout.addWidget(self.overwrite_checkbox)
        layout.addWidget(behavior_group)
        
        perf_group = QGroupBox("性能设置")
        perf_layout = QGridLayout(perf_group)
        perf_layout.addWidget(QLabel("最大并发处理任务数:"), 0, 0)
        self.max_workers_spin = QSpinBox()
        self.max_workers_spin.setRange(1, multiprocessing.cpu_count() * 2)
        perf_layout.addWidget(self.max_workers_spin, 0, 1)
        perf_layout.setColumnStretch(2, 1)
        layout.addWidget(perf_group)

        ffmpeg_group = QGroupBox("FFmpeg 工具")
        ffmpeg_layout = QHBoxLayout(ffmpeg_group)
        self.check_ffmpeg_btn = QPushButton("检查 FFmpeg 环境")
        ffmpeg_layout.addWidget(self.check_ffmpeg_btn)
        ffmpeg_layout.addStretch()
        layout.addWidget(ffmpeg_group)

        layout.addStretch()
        return settings_tab

    def connect_signals(self):
        self.drag_area.files_dropped.connect(self.handle_dropped_files)
        self.add_files_btn.clicked.connect(self.select_files)
        self.add_folder_btn.clicked.connect(self.select_folder)
        self.clear_files_btn.clicked.connect(self.clear_file_list)
        self.remove_selected_btn.clicked.connect(self.remove_selected_files)
        
        self.percent_radio.toggled.connect(self.update_ui_states)
        self.save_new_checkbox.toggled.connect(self.update_ui_states)
        self.browse_output_btn.clicked.connect(self.browse_output_dir)
        
        self.start_btn.clicked.connect(self.start_processing)
        self.stop_btn.clicked.connect(self.stop_processing)
        self.check_ffmpeg_btn.clicked.connect(self.check_ffmpeg)
        
        self.clear_log_btn.clicked.connect(self.log_view.clear)
        self.save_log_btn.clicked.connect(self.save_log)

    def handle_dropped_files(self, paths):
        supported_exts = self.get_supported_extensions()
        for path in paths:
            if os.path.isdir(path):
                self.add_directory(path, supported_exts)
            elif os.path.isfile(path):
                if path.lower().endswith(tuple(supported_exts)):
                    self.add_file_to_list(path)
    
    def select_files(self):
        supported_exts = self.get_supported_extensions()
        filter_str = f"视频文件 ({' '.join(['*' + ext for ext in supported_exts])})"
        files, _ = QFileDialog.getOpenFileNames(self, "选择视频文件", "", filter_str)
        for f in files:
            self.add_file_to_list(f)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            self.add_directory(folder, self.get_supported_extensions())

    def add_directory(self, path, supported_exts):
        for root, _, files in os.walk(path):
            for file in files:
                if file.lower().endswith(tuple(supported_exts)):
                    self.add_file_to_list(os.path.join(root, file))

    def add_file_to_list(self, path):
        if path not in self.files:
            self.files.add(path)
            self.file_list.addItem(QListWidgetItem(path))
    
    def clear_file_list(self):
        if self.threadpool.activeThreadCount() > 0:
            QMessageBox.warning(self, "正在处理", "请先停止当前处理任务。")
            return
        self.file_list.clear()
        self.files.clear()

    def remove_selected_files(self):
        if self.threadpool.activeThreadCount() > 0:
            QMessageBox.warning(self, "正在处理", "请先停止当前处理任务。")
            return
        for item in self.file_list.selectedItems():
            self.files.discard(item.text())
            self.file_list.takeItem(self.file_list.row(item))

    def get_supported_extensions(self):
        exts = [ext for ext, chk in self.format_checks.items() if chk.isChecked()]
        exts.extend(self.custom_format_widget.get_formats())
        return exts

    def start_processing(self):
        if self.threadpool.activeThreadCount() > 0:
            QMessageBox.warning(self, "正在处理", "已有任务在运行中。")
            return
        
        if not self.files:
            QMessageBox.warning(self, "无文件", "请先添加要处理的视频文件。")
            return

        if self.save_new_checkbox.isChecked():
            out_dir = self.output_dir_input.text()
            if not out_dir or not os.path.isdir(out_dir):
                QMessageBox.warning(self, "无效目录", "请选择一个有效的输出目录。")
                return

        self.save_ui_to_config()
        self.threadpool.setMaxThreadCount(self.cfg['max_workers'])
        
        self.log_view.append(f"--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 开始新的处理任务 ---")
        self.log_view.append(f"线程池最大并发数: {self.threadpool.maxThreadCount()}")
        self.log_view.append(f"找到 {len(self.files)} 个文件待处理。")
        
        self.progress_bar.setMaximum(len(self.files))
        self.progress_bar.setValue(0)
        self.active_workers = len(self.files)
        self.set_controls_enabled(False)

        for video_path in self.files:
            worker = Worker(video_path, self.cfg)
            worker.signals.progress.connect(self.log_file_status)
            worker.signals.finished.connect(self.on_worker_finished)
            worker.signals.error.connect(lambda e: self.log_file_status("错误", e))
            self.threadpool.start(worker)

    def stop_processing(self):
        self.log_view.append("...正在请求停止所有任务...")
        self.threadpool.clear() # 清除队列中未开始的任务
        self.threadpool.waitForDone() # 等待当前活动任务自然结束（不会强制中断）
        self.on_all_workers_finished()


    def log_file_status(self, filename, status):
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.log_view.append(f"[{timestamp}] {filename}: {status}")

    def on_worker_finished(self):
        """每个工作线程完成时调用"""
        self.active_workers -= 1
        processed_count = self.progress_bar.maximum() - self.active_workers
        self.progress_bar.setValue(processed_count)
        if self.active_workers == 0:
            self.on_all_workers_finished()

    def on_all_workers_finished(self):
        """所有工作线程都完成后调用"""
        self.log_view.append(f"--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 处理任务结束 ---\n")
        self.set_controls_enabled(True)
        QMessageBox.information(self, "完成", "所有文件处理完毕！")
        
    def set_controls_enabled(self, enabled):
        self.start_btn.setEnabled(enabled)
        self.stop_btn.setEnabled(not enabled)
        self.progress_bar.setVisible(not enabled)

    def update_ui_states(self):
        self.percent_spin.setEnabled(self.percent_radio.isChecked())
        self.time_spin.setEnabled(not self.percent_radio.isChecked())
        
        is_save_new = self.save_new_checkbox.isChecked()
        self.output_dir_input.setEnabled(is_save_new)
        self.browse_output_btn.setEnabled(is_save_new)

    def browse_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if directory:
            self.output_dir_input.setText(directory)

    def check_ffmpeg(self):
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
            QMessageBox.information(self, "成功", "FFmpeg 环境配置正确！")
        except (FileNotFoundError, subprocess.CalledProcessError):
            QMessageBox.critical(self, "失败", "未找到 FFmpeg！\n请确保已正确安装并将其添加至系统环境变量 (PATH)。")

    def save_log(self):
        log_content = self.log_view.toPlainText()
        if not log_content:
            QMessageBox.information(self, "空日志", "没有日志内容可以保存。")
            return
        
        path, _ = QFileDialog.getSaveFileName(self, "保存日志", LOG_FILE, "Text Files (*.txt)")
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(log_content)
            except Exception as e:
                QMessageBox.critical(self, "保存失败", f"无法写入文件：\n{e}")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = DEFAULT_CONFIG.copy()
                    config.update(json.load(f))
                    return config
            except (json.JSONDecodeError, IOError):
                return DEFAULT_CONFIG.copy()
        return DEFAULT_CONFIG.copy()

    def save_ui_to_config(self):
        self.cfg['extensions'] = [ext for ext, chk in self.format_checks.items() if chk.isChecked()]
        self.cfg['custom_extensions'] = self.custom_format_widget.get_formats()
        self.cfg['use_percent'] = self.percent_radio.isChecked()
        self.cfg['percent_value'] = self.percent_spin.value()
        self.cfg['time_seconds'] = self.time_spin.value()
        self.cfg['save_to_new_dir'] = self.save_new_checkbox.isChecked()
        self.cfg['output_dir'] = self.output_dir_input.text()
        self.cfg['overwrite_existing'] = self.overwrite_checkbox.isChecked() 
        self.cfg['max_workers'] = self.max_workers_spin.value()
        
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=4)
        except IOError:
            self.log_file_status("配置", "保存失败，请检查权限。")

    def load_settings_to_ui(self):
        for ext, chk in self.format_checks.items():
            chk.setChecked(ext in self.cfg.get('extensions', []))
        self.custom_format_widget.set_formats(self.cfg.get('custom_extensions', []))
        
        if self.cfg.get('use_percent', True):
            self.percent_radio.setChecked(True)
        else:
            self.time_radio.setChecked(True)
        self.percent_spin.setValue(self.cfg.get('percent_value', 20.0))
        self.time_spin.setValue(self.cfg.get('time_seconds', 20))
        
        self.save_new_checkbox.setChecked(self.cfg.get('save_to_new_dir', False))
        self.output_dir_input.setText(self.cfg.get('output_dir', ''))
        
        self.overwrite_checkbox.setChecked(self.cfg.get('overwrite_existing', False))

        self.max_workers_spin.setValue(self.cfg.get('max_workers', multiprocessing.cpu_count()))
        
        self.update_ui_states()

    def closeEvent(self, event):
        self.save_ui_to_config()
        if self.threadpool.activeThreadCount() > 0:
            reply = QMessageBox.question(self, "确认退出", "仍在处理任务，确定要退出吗？", 
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.threadpool.clear()
                self.threadpool.waitForDone()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    def set_stylesheet(self):
        self.setStyleSheet("""
            QWidget {
                font-family: 'Microsoft YaHei UI', 'Segoe UI', Arial, sans-serif;
                font-size: 14px;
                background-color: #f0f2f5;
            }
            QGroupBox {
                font-weight: bold;
                background-color: #ffffff;
                border: 1px solid #d9d9d9;
                border-radius: 8px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 10px;
                left: 10px;
                color: #005a9e;
            }
            #TitleLabel {
                font-size: 24px;
                font-weight: bold;
                color: #005a9e;
                padding: 10px;
            }
            QTabWidget::pane {
                border: 1px solid #d9d9d9;
                border-radius: 8px;
                background-color: #ffffff;
            }
            QTabBar::tab {
                background-color: #e1eaf0;
                padding: 10px 25px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 2px;
                color: #333;
            }
            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #0078d4;
                border-bottom: 2px solid #0078d4;
            }
            QPushButton {
                background-color: #0078d4;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 5px;
            }
            QPushButton:hover { background-color: #106ebe; }
            QPushButton:pressed { background-color: #005a9e; }
            QPushButton:disabled { background-color: #cccccc; }
            #SuccessButton { background-color: #28a745; }
            #SuccessButton:hover { background-color: #218838; }
            #DangerButton { background-color: #dc3545; }
            #DangerButton:hover { background-color: #c82333; }
            #WarningButton { background-color: #ffc107; color: #212529; }
            #WarningButton:hover { background-color: #e0a800; }
            
            QLineEdit, QSpinBox, QDoubleSpinBox, QListWidget, QTextEdit {
                border: 1px solid #d9d9d9;
                border-radius: 5px;
                padding: 8px;
                background-color: #ffffff;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QListWidget:focus {
                border-color: #0078d4;
            }
            QProgressBar {
                border: 1px solid #d9d9d9;
                border-radius: 5px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #0078d4;
                border-radius: 4px;
            }
        """)

# --- 程序入口 ---
if __name__ == '__main__':
    multiprocessing.freeze_support()
    
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    window = MainApp()
    window.show()
    sys.exit(app.exec_())
