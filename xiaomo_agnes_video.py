import requests
# 全局统一requests默认重试次数，与Adapter保持一致
requests.adapters.DEFAULT_RETRIES = 8
import time
import json
import tempfile
import os
import torch
import numpy as np
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import cv2
import urllib3
from io import BytesIO
# 彻底屏蔽HTTPS不安全证书警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -------------------------- API 固定常量 --------------------------
BASE_CREATE_URL = "https://apihub.agnes-ai.com/v1/videos"
BASE_VIDEO_QUERY_URL = "https://apihub.agnes-ai.com/agnesapi"
MODEL_NAME = "agnes-video-v2.0"

# -------------------------- 请求会话（强制忽略系统代理/VPN） --------------------------
def get_http_session():
    session = requests.Session()
    session.trust_env = False
    retry = Retry(
        total=12,
        backoff_factor=4,
        status_forcelist=[429, 500, 502, 503, 504, 408, 520, 521, 522],
        allowed_methods=["GET", "POST", "HEAD"],
        raise_on_status=False
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=20,
        pool_maxsize=20,
        pool_block=True
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    
    # 连接保活
    session.headers.update({
        "Connection": "keep-alive",
        "Keep-Alive": "timeout=180, max=100"
    })
    return session

# -------------------------- 最终兼容版ComfyUI标准视频包装类 --------------------------
class ComfyVideoWrapper:
    def __init__(self, tensor):
        self.tensor = tensor
        self.batch, self.frames, self.height, self.width, self.channels = tensor.shape

    def get_dimensions(self):
        return (self.width, self.height)

    def save_to(self, filepath, frame_rate=24, format="mp4", codec="libx264", **kwargs):
        import imageio
        writer_kwargs = {}
        writer_kwargs["fps"] = frame_rate if frame_rate and frame_rate > 0 else 24
        if format and isinstance(format, str) and format.strip() and format.lower() != "auto":
            writer_kwargs["format"] = format.strip()
        if codec and isinstance(codec, str) and codec.strip() and codec.lower() != "auto":
            writer_kwargs["codec"] = codec.strip()

        frames_np = self.tensor.squeeze(0).clamp(0.0, 1.0).cpu().numpy()
        frames_np = (frames_np * 255).astype(np.uint8)

        if frames_np.shape[-1] == 4:
            frames_np = frames_np[..., :3]
        elif frames_np.shape[-1] not in (1, 2, 3, 4):
            raise ValueError(f"不支持的视频通道数: {frames_np.shape[-1]}，仅支持1/2/3/4通道")

        writer = imageio.get_writer(filepath, **writer_kwargs)
        try:
            for frame in frames_np:
                writer.append_data(frame)
        finally:
            writer.close()

    def __getitem__(self, idx):
        return self.tensor[idx]

    @property
    def shape(self):
        return self.tensor.shape

# -------------------------- 自定义节点主体 --------------------------
class XiaoMoAgnesVideo:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "api_key": ("STRING", {"multiline": False, "placeholder": "sk-粘贴你的Agnes API密钥"}),
                "img_url": ("STRING", {"multiline": True, "placeholder": "图生视频填公网图片HTTPS直链，文生视频留空", "default": ""}),
                "prompt_ext": ("STRING", {"multiline": False, "placeholder": "【外接扩展输入】可接入大模型文本节点，自动生成/优化正向提示词，为空则使用下方prompt", "default": ""}),
                "prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": """=====【正向提示词全题材通用编写规范】=====
一、文生视频（无参考图）标准结构：主体 + 细微动作 + 场景环境 + 光影氛围 + 镜头运镜 + 画质风格
1.真人/写实模板：年轻女生，缓慢抬手撩发，城市黄昏街道，柔和逆光，缓慢平移镜头，8k写实电影质感，人物全程五官不变，无画面抖动
2.动漫/二次元模板：日系少女，轻轻摆动裙摆，樱花街道，柔和马卡龙光影，固定机位，细腻赛璐璐上色，人物五官发型全程统一
3.科幻/游戏模板：机甲战士，缓步移动，赛博雨夜都市，霓虹漫反射，推拉镜头，高细节3A游戏画质，机甲造型不畸变
4.产品/静物模板：陶瓷水杯，轻微水雾流动，原木桌面，柔光侧拍，缓慢环绕运镜，产品轮廓完全不变
5.动物通用模板：猫咪轻轻晃动尾巴，窗边柔光，微小呼吸起伏，固定机位，毛发纹理稳定

二、图生视频（上传图片必看）
只写画面微动，禁止修改原图人物/动漫形象/物体/背景
示例：动漫人物发丝随风轻飘，窗外灯光明暗渐变，全程保持原图画风、人物五官、服饰完全一致

三、通用加分关键词：平滑微运动、主体高度稳定、无画面闪烁、电影级光影、超高细节"""
                }),
                "neg_prompt_ext": ("STRING", {"multiline": False, "placeholder": "【外接扩展输入】可接入大模型文本，自动生成反向词，为空读取下方neg_prompt", "default": ""}),
                "neg_prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": """=====【全题材通用反向提示规范】=====
# 真人/动漫共用人物崩坏（最高优先级）
面部扭曲、五官错位、眼睛变形、多眼多嘴、肢体穿插、手脚畸形、人物样貌突变、动漫脸型崩坏、线条扭曲

# 画面动态缺陷
画面剧烈闪烁、画面抖动、光影跳变、物体凭空消失/新增、镜头剧烈晃动

# 画质与结构瑕疵
模糊、低分辨率、水印文字、色块撕裂、透视错乱、物体融化、线条崩坏、动漫上色溢色

# 题材专属避雷
动漫：画风突变、轮廓变形、色块断层；真人：皮肤畸形；科幻：机甲结构崩坏；动物：五官扭曲"""
                }),
                "width": ("INT", {"default": 1152, "min": 512, "max": 2048, "step": 64}),
                "height": ("INT", {"default": 768, "min": 512, "max": 2048, "step": 64}),
                "num_frames": ("INT", {"default": 81, "min": 81, "max": 441, "step": 8}),
                "frame_rate": ("INT", {"default": 24, "min": 1, "max": 60}),
                "seed": ("INT", {"default": 123456}),
                "seed_mode": (["randomize", "fixed"], {"default": "randomize"}),
                "poll_interval": ("INT", {"default": 8, "min": 2, "max": 20}),
            }
        }

    RETURN_TYPES = ("VIDEO", "STRING")
    RETURN_NAMES = ("视频输出", "完整任务JSON")
    FUNCTION = "run"
    CATEGORY = "肖默定制插件"

    def run(self, api_key, img_url, prompt_ext, prompt, neg_prompt_ext, neg_prompt,
            width, height, num_frames, frame_rate, seed, seed_mode, poll_interval):
        final_prompt = prompt_ext.strip() or prompt.strip()
        final_neg = neg_prompt_ext.strip() or neg_prompt.strip()
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": MODEL_NAME,
            "prompt": final_prompt,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "frame_rate": frame_rate,
        }
        img_clean = img_url.strip()
        if img_clean:
            payload["image"] = img_clean
        if final_neg:
            payload["negative_prompt"] = final_neg
        if seed_mode == "fixed":
            payload["seed"] = seed

        full_task_json = {}
        video_id = None
        video_url = None
        printed_vid_flag = False

        def cut_text(text, limit=80):
            return text[:limit] + "..." if len(text) > limit else text

        # ========== 1. 创建任务 ==========
        print("🚀【步骤1/4】正在提交 Agnes V2.0 视频生成任务...")
        print(f"📝 正向提示词：{cut_text(final_prompt)}")
        if final_neg:
            print(f"🚫 反向提示词：{cut_text(final_neg)}")
        if img_clean:
            print(f"🖼️ 参考图链接：{cut_text(img_clean)}")
        print(f"📐 分辨率 {width}×{height}，总帧数 {num_frames}，帧率 {frame_rate}FPS")

        session = get_http_session()
        try:
            resp = session.post(BASE_CREATE_URL, headers=headers, json=payload, timeout=(30, 300), verify=False)
            resp.raise_for_status()
            task_data = resp.json()
            full_task_json = task_data
            err_msg = task_data.get("error")
            if err_msg is not None and str(err_msg).strip() != "":
                raise Exception(f"【创建任务业务报错】{err_msg}")
            video_id = task_data.get("video_id") or task_data.get("task_id")
            if not video_id:
                raise Exception("接口返回数据异常：未获取到video_id/task_id")
            print(f"✅ 任务创建成功，video_id: {video_id}")
        except requests.exceptions.ReadTimeout:
            raise Exception("【网络读取超时】提交任务请求读取超时")
        except requests.exceptions.ConnectTimeout:
            raise Exception("【网络连接超时】无法连接API服务器，检查代理/网络")
        except requests.exceptions.RequestException as e:
            raise Exception(f"【创建任务网络请求失败】{str(e)}")

        # ========== 2. 轮询生成进度 ==========
        print("\n⏳【步骤2/4】开始轮询任务生成进度，最长等待20分钟...")
        print("💡 提示：视频生成通常需要 3~15 分钟，请耐心等待！")
        
        waited = 0
        max_wait = 1200
        
        while waited < max_wait:
            time.sleep(poll_interval)
            waited += poll_interval
            
            try:
                poll_session = get_http_session()
                query_params = {"video_id": video_id, "model_name": MODEL_NAME}
                
                stat_resp = poll_session.get(BASE_VIDEO_QUERY_URL, headers=headers, params=query_params, timeout=(20, 90), verify=False)
                stat_resp.raise_for_status()
                task_info = stat_resp.json()
                full_task_json = task_info
                
                err_msg = task_info.get("error")
                if err_msg is not None and str(err_msg).strip() != "":
                    raise Exception(f"【轮询任务业务报错】{err_msg}")
                
                status = task_info.get("status", "")
                progress = task_info.get("progress", 0)
                
                if not printed_vid_flag:
                    print(f"📌 当前查询任务ID：{video_id}")
                    printed_vid_flag = True
                
                print(f"⏱️ 任务状态: {status} | 进度: {progress}% | 已等待 {waited}s / {max_wait}s")
                
                if status == "completed":
                    video_url = task_info.get("remixed_from_video_id") or task_info.get("url") or task_info.get("video_url")
                    if not video_url:
                        raise Exception("任务已完成，但接口未返回视频下载链接")
                    print("🎉 视频生成完成，准备下载文件...")
                    break
                elif status == "failed":
                    err = task_info.get("error") or "无详细失败原因"
                    raise Exception(f"【视频生成失败】{err}")
                    
            except requests.exceptions.ReadTimeout:
                print(f"⚠️ 单次轮询读取超时，{poll_interval}秒后自动重试...")
                continue
            except requests.exceptions.ConnectTimeout:
                print(f"⚠️ 单次轮询连接服务器超时，{poll_interval}秒后自动重试...")
                continue
            except requests.exceptions.RequestException as e:
                print(f"⚠️ 轮询请求异常（常见，重试中）：{str(e)[:120]}")
                continue
            except Exception as e:
                print(f"⚠️ 轮询其他异常：{e}，继续重试...")
                continue

        if waited >= max_wait and not video_url:
            raise Exception(f"任务轮询超时：已等待 {max_wait/60} 分钟仍未生成完成，请稍后重试")

        # ========== 3. 下载视频 ==========
        print("\n📥【步骤3/4】正在下载视频文件...")
        try:
            download_session = get_http_session()
            vid_resp = download_session.get(video_url, timeout=(30, 180), verify=False, stream=True)
            vid_resp.raise_for_status()
            
            vid_bytes = b""
            for chunk in vid_resp.iter_content(chunk_size=8192):
                if chunk:
                    vid_bytes += chunk
            
            print(f"✅ 视频下载完成，文件大小：{round(len(vid_bytes)/1024/1024, 2)} MB")
        except requests.exceptions.ReadTimeout:
            raise Exception("【视频下载读取超时】服务器响应缓慢，请切换网络重试")
        except requests.exceptions.ConnectTimeout:
            raise Exception("【视频下载连接超时】无法访问视频资源地址")
        except requests.exceptions.RequestException as e:
            raise Exception(f"【视频下载请求失败】{str(e)}")

        # ========== 4. 双方案解码 ==========
        print("\n🔧【步骤4/4】解析视频帧，转换ComfyUI标准张量...")
        frames = []
        temp_file_path = ""
        decode_success = False

        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp.write(vid_bytes)
                tmp.flush()
                temp_file_path = tmp.name
            cap = cv2.VideoCapture(temp_file_path)
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frame_np = np.array(frame_rgb, dtype=np.float32) / 255.0
                frames.append(frame_np)
            cap.release()
            if frames:
                decode_success = True
        except Exception:
            print("⚠️ OpenCV解码失败，切换imageio内存解码...")
        finally:
            if os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except:
                    pass

        if not decode_success:
            try:
                import imageio
                stream = BytesIO(vid_bytes)
                reader = imageio.get_reader(stream, format="mp4")
                frames.clear()
                for frame in reader:
                    frame_np = np.array(frame, dtype=np.float32) / 255.0
                    frames.append(frame_np)
                reader.close()
                decode_success = True
            except ImportError:
                raise Exception("OpenCV解码失败，且未安装imageio！执行 pip install imageio imageio-ffmpeg 后重试")
            except Exception as e:
                raise Exception(f"两套解码方案全部失败：{str(e)}")

        if not frames:
            raise Exception("视频解码失败，未读取到任何有效画面帧")

        frames_np = np.stack(frames, axis=0)
        raw_tensor = torch.from_numpy(frames_np).unsqueeze(0)
        video_out = ComfyVideoWrapper(raw_tensor)

        print(f"✅ 全部处理完成！成功解析 {len(frames)} 帧，分辨率 {video_out.width}×{video_out.height}")

        try:
            output_json = json.dumps(full_task_json, ensure_ascii=False, indent=2)
        except Exception:
            output_json = json.dumps({"info": "任务数据序列化失败"}, ensure_ascii=False)

        return (video_out, output_json)

# 节点注册
NODE_CLASS_MAPPINGS = {"XiaoMoAgnesVideo": XiaoMoAgnesVideo}
NODE_DISPLAY_NAME_MAPPINGS = {"XiaoMoAgnesVideo": "肖默 - Agnes V2.0 图生视频节点"}