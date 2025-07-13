import os
import subprocess
import tempfile
import shutil
import json


# --- 配置常量 ---
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.mov')
THUMBNAIL_TIME_SECONDS = 20

def check_ffmpeg():
    """检查 ffmpeg 和 ffprobe 是否已安装并配置到环境变量中。"""
    for tool in ['ffmpeg']:
        try:
            subprocess.run([tool, '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            print(f"错误：未检测到 '{tool}' 命令。请确保已正确安装 FFmpeg 并添加到系统环境变量中。")
            print("下载地址：https://ffmpeg.org/download.html")
            sys.exit(1)


def has_embedded_cover(video_path: str) -> bool:
    """
    使用 ffprobe 检查视频文件是否已嵌入封面。
    返回: True 如果已存在封面, 否则返回 False。
    """
    print("  - 检查是否存在封面...")
    command = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_streams', video_path
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8')
        data = json.loads(result.stdout)
        # 遍历文件中的所有流
        for stream in data.get('streams', []):
            # 检查 'disposition' 字典中 'attached_pic' 键的值是否为1
            if stream.get('disposition', {}).get('attached_pic', 0) == 1:
                return True
        return False
    except Exception:
        # 如果 ffprobe 出错或文件有问题，安全起见返回False，让主流程决定如何处理
        return False

def get_video_duration(video_path: str) -> float:
    """使用ffprobe安全地获取视频时长。如果失败则返回0。"""
    command = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_format', video_path
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8')
        data = json.loads(result.stdout)
        return float(data.get('format', {}).get('duration', 0))
    except Exception:
        return 0

def process_video(video_path: str):
    """对单个视频文件执行封面嵌入的核心处理函数。"""
    filename = os.path.basename(video_path)
    print(f"\n▶ 开始处理文件: {filename}")

    # --- 新增的判断逻辑 ---
    if has_embedded_cover(video_path):
        print(f"  - [跳过] '{filename}' 已包含封面。")
        return
    else:
        print("     未发现封面，继续处理。")
    # --- 判断逻辑结束 ---

    # 1. 检查视频时长
    duration = get_video_duration(video_path)
    if duration < THUMBNAIL_TIME_SECONDS:
        print(f"  - [跳过] 视频时长 ({duration:.1f}s) 小于 {THUMBNAIL_TIME_SECONDS}s。")
        return

    temp_cover_path = ""
    temp_output_path = ""
    
    try:
        # --- 步骤 1: 提取封面 ---
        print(f"  - 步骤 1/4: 正在从 {THUMBNAIL_TIME_SECONDS}s 处提取封面...")
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp_cover_file:
            temp_cover_path = temp_cover_file.name
        
        extract_command = [
            'ffmpeg', '-i', video_path, '-ss', str(THUMBNAIL_TIME_SECONDS),
            '-vframes', '1', '-q:v', '2', '-y', '-hide_banner', temp_cover_path
        ]
        subprocess.run(extract_command, check=True, capture_output=True)

        if not os.path.exists(temp_cover_path) or os.path.getsize(temp_cover_path) == 0:
            raise RuntimeError("提取封面失败，未生成有效的图片文件。")
        print("     提取成功。")

        # --- 步骤 2: 嵌入封面 ---
        print("  - 步骤 2/4: 正在嵌入封面...")
        base_name, extension = os.path.splitext(video_path)
        temp_output_path = f"{base_name}.temp_video{extension}"
        
        embed_command = [
            'ffmpeg', '-i', video_path, '-i', temp_cover_path,
            '-map', '0:v:0', '-map', '0:a?', '-map', '1',
            '-c', 'copy', '-c:v:1', 'png',
            '-disposition:v:1', 'attached_pic', '-y', '-hide_banner', temp_output_path
        ]
        subprocess.run(embed_command, check=True, capture_output=True)
        print("     嵌入成功。")

        # --- 步骤 3: 替换原文件 ---
        print("  - 步骤 3/4: 正在用新文件替换原始文件...")
        shutil.move(temp_output_path, video_path)
        print("     替换成功。")

        print(f"✔ [成功] '{filename}' 已处理完毕。")

    except Exception as e:
        print(f"❌ [失败] 处理 '{filename}' 时发生错误。")
        if isinstance(e, subprocess.CalledProcessError):
            error_output = e.stderr.decode('utf-8', errors='ignore')
            print("     --- FFmpeg 错误日志 ---\n" + error_output)
        else:
            print(f"     --- Python 错误日志 ---\n{e}")

    finally:
        # --- 步骤 4: 清理临时文件 ---
        print("  - 步骤 4/4: 正在清理临时文件...")
        if os.path.exists(temp_cover_path):
            os.remove(temp_cover_path)
        if os.path.exists(temp_output_path):
            os.remove(temp_output_path)

def main():
    """主入口函数，负责用户交互和任务分派。"""
    input_path = input("请输入视频文件或根文件夹的路径: ").strip().strip('"\'')
    
    if not os.path.exists(input_path):
        print(f"错误: 路径 '{input_path}' 不存在。")
        return

    print("\n" + "="*50)
    print("警告：此脚本将就地修改您的原始视频文件！")
    print("操作无法撤销，请务必在运行前备份重要数据。")
    print("="*50)
    
    confirm = input("您确定要继续吗? (Y/N): ").lower().strip()
    if confirm not in ['y', 'yes']:
        print("操作已取消。")
        return

    if os.path.isfile(input_path):
        if input_path.lower().endswith(VIDEO_EXTENSIONS):
            process_video(input_path)
        else:
            print("错误：输入的单个文件不是支持的视频格式。")
    elif os.path.isdir(input_path):
        print(f"\n开始递归扫描文件夹: {input_path}")
        for dirpath, _, filenames in os.walk(input_path):
            for filename in filenames:
                if filename.lower().endswith(VIDEO_EXTENSIONS):
                    video_path = os.path.join(dirpath, filename)
                    process_video(video_path)
    
    print("\n--- 所有任务已完成 ---")

if __name__ == "__main__":
    check_ffmpeg()
    main()
