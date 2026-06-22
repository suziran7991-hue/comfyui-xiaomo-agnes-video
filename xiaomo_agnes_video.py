import requests
import json
import time

class XiaoMo_AgnesV2Image2Video:
    # 菜单分类（展示中文，合规）
    CATEGORY = "肖默定制插件/Agnes视频API"

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "api_key": (
                    "STRING",
                    {
                        "multiline": False,
                        "default": "sk-粘贴你的Agnes API密钥",
                        "display_name": "肖默_API密钥"
                    }
                ),
                "image_url": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "https://xxx/xxx.png",
                        "display_name": "肖默_图片公网HTTPS链接"
                    }
                ),
                "prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "流畅自然运动，人物主体稳定无变形，无画面闪烁，电影运镜，高细节",
                        "display_name": "肖默_正向提示词"
                    }
                ),
                "negative_prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "面部扭曲、画面闪烁、肢体变形、模糊、崩坏结构、水印",
                        "display_name": "肖默_反向提示词"
                    }
                ),
                "width": (
                    "INT",
                    {
                        "default": 1152,
                        "min": 512,
                        "max": 1920,
                        "display_name": "肖默_视频宽度"
                    }
                ),
                "height": (
                    "INT",
                    {
                        "default": 768,
                        "min": 384,
                        "max": 1080,
                        "display_name": "肖默_视频高度"
                    }
                ),
                "num_frames": (
                    "INT",
                    {
                        "default": 121,
                        "min": 9,
                        "max": 441,
                        "display_name": "肖默_总帧数(8n+1)"
                    }
                ),
                "frame_rate": (
                    "INT",
                    {
                        "default": 24,
                        "min": 1,
                        "max": 60,
                        "display_name": "肖默_帧率FPS"
                    }
                ),
                "seed": (
                    "INT",
                    {"default": 123456, "display_name": "肖默_随机种子"}
                ),
                "poll_interval": (
                    "INT",
                    {
                        "default": 5,
                        "min": 3,
                        "max": 20,
                        "display_name": "肖默_轮询间隔(秒)"
                    }
                ),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("肖默_视频MP4下载直链", "肖默_完整任务返回JSON")
    FUNCTION = "generate_video_task"

    def generate_video_task(
        self,
        api_key,
        image_url,
        prompt,
        negative_prompt,
        width,
        height,
        num_frames,
        frame_rate,
        seed,
        poll_interval
    ):
        base_create_url = "https://apihub.agnes-ai.com/v1/videos"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        # 帧数校验
        if (num_frames - 1) % 8 != 0:
            return ("", f"【肖默专属节点提示】错误：总帧数必须满足8n+1，可用：81/121/241/441")

        payload = {
            "model": "agnes-video-v2.0",
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "image": image_url,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "frame_rate": frame_rate,
            "seed": seed
        }

        # 提交任务
        try:
            resp = requests.post(base_create_url, headers=headers, json=payload, timeout=30)
            task_json = resp.json()
        except Exception as e:
            return ("", f"【肖默专属节点提示】任务提交异常：{str(e)}")

        if resp.status_code != 200:
            return ("", json.dumps(task_json, indent=2, ensure_ascii=False))

        video_id = task_json.get("video_id")
        if not video_id:
            return ("", json.dumps(task_json, indent=2, ensure_ascii=False))

        # 轮询查询
        query_url = f"https://apihub.agnes-ai.com/agnesapi?video_id={video_id}&model_name=agnes-video-v2.0"
        while True:
            try:
                query_resp = requests.get(query_url, headers=headers, timeout=30)
                res_json = query_resp.json()
            except Exception as e:
                return ("", f"【肖默专属节点提示】查询异常：{str(e)}")

            status = res_json.get("status")
            if status == "completed":
                mp4_url = res_json.get("remixed_from_video_id", "")
                return (mp4_url, json.dumps(res_json, indent=2, ensure_ascii=False))
            if status == "failed":
                return ("【肖默专属节点提示】视频生成失败", json.dumps(res_json, indent=2, ensure_ascii=False))
            time.sleep(poll_interval)

# 节点注册映射（底层key纯英文，展示名带肖默）
NODE_CLASS_MAPPINGS = {
    "XiaoMo_AgnesV2Image2Video": XiaoMo_AgnesV2Image2Video
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "XiaoMo_AgnesV2Image2Video": "肖默 - Agnes V2.0 图生视频节点"
}