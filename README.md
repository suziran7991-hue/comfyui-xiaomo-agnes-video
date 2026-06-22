# comfyui-xiaomo-agnes-video
## 肖默定制 Agnes V2.0 图生视频API节点
专属标识：肖默，适配 Agnes-Video-V2.0 官方异步API，一键自动化工作流，无需拼接多个请求节点

## 功能介绍
1. 图生视频能力，适配新版 apihub.agnes-ai.com 域名
2. 内置帧数规则校验，强制遵循 `8n+1` 规范（81/121/241/441），提前拦截错误参数
3. 自动轮询查询生成进度，排队、渲染全程自动等待，无需手动多次GET请求
4. 界面全节点携带专属标记「肖默」，私有化辨识度高，便于区分自用定制插件
5. 一键输出视频MP4直链，搭配通用下载节点即可保存本地视频文件
# 更新日志
## V1.1 最新版本（当前已上传）
1. 提示词双输入升级：新增「外接正向提示词」「外接反向提示词」单行输入插槽，可连线外部文本/提示词节点，外部内容优先级高于内置多行文本框；
2. 输出原生适配ComfyUI：新增VIDEO视频输出端口，可直接连接 Preview Video / Save Video 原生节点，自动预览、保存MP4，无需手动复制视频链接；
3. 完善全局异常容错：所有网络请求（创建任务、轮询、下载视频）增加超时、断网、接口报错捕获，弹出中文清晰提示，不会直接崩溃；
4. 增加数据校验：任务完成后校验视频下载链接是否存在，避免KeyError程序中断；
5. 界面视觉优化：外接提示词使用单行输入框，和内置多行提示词文本框区分，操作辨识度更高；
6. 保留完整任务JSON输出端口，用于查看接口原始日志、定位画面崩坏/生成失败问题。
## 安装教程
### 方式1：ComfyUI Manager 一键安装（插件上架后可用）
1. 打开 ComfyUI Manager → Install Custom Nodes
2. 搜索插件名：`xiaomo-agnes-video`
3. 点击 Install，等待自动安装依赖 `requests`，重启ComfyUI生效

### 方式2：手动离线安装
1. 打开 ComfyUI 目录下 `custom_nodes` 文件夹
2. 执行Git克隆命令：
```bash
git clone https://github.com/suziran7991-hue/comfyui-xiaomo-agnes-video.git
```

## GitHub访问超时/443端口连接失败解决办法
### 1. 全新安装使用镜像加速克隆（推荐）
打开 custom_nodes 目录CMD执行：
```bash
git clone https://mirror.ghproxy.com/https://github.com/suziran7991-hue/comfyui-xiaomo-agnes-video.git
```
### 2. 已安装插件更新失败
进入插件文件夹执行：
```bash
git remote set-url origin https://mirror.ghproxy.com/https://github.com/suziran7991-hue/comfyui-xiaomo-agnes-video.git
```
```bash
git pull
```
### 3. 镜像依旧无法连接（离线备用方案）
1. 前往项目主页点击 Code → Download ZIP 下载源码压缩包；
2. 删除原有 comfyui-xiaomo-agnes-video 文件夹；
3. 解压压缩包，文件夹重命名为 comfyui-xiaomo-agnes-video 放入 custom_nodes；
4. 重启ComfyUI完成更新。
## 使用说明
在输入框 肖默_API密钥 填写你个人的 Agnes 平台 API Key
肖默_图片公网HTTPS链接 填入图床上传后的公开图片地址，本地图片文件路径无法读取，必须填写公网图片链接
帧数仅支持标准数值：81、121、241、441，其他数字会弹窗提示错误
正向提示词描述画面运动、镜头风格；反向提示词屏蔽崩坏、闪烁、变形等问题
输出端口1：原生VIDEO视频流，直连预览/保存节点自动导出MP4；
输出端口2：完整任务JSON日志，用于查看接口原始数据、排查报错
依赖说明
插件自动依赖 requests 网络库，Manager 安装时会自动部署，手动安装缺失可执行：

### Windows便携通用安装依赖方法
- 1. 打开ComfyUI根目录（python_embeded所在的文件夹）CMD，执行：
```bash
python_embeded\python.exe -m pip install requests>=2.30.0
```
- 2. 如果提示找不到路径，使用完整绝对路径示例（按自己盘符修改）
D:\ComfyUI\python_embeded\python.exe -m pip install requests>=2.30.0

- 3. 若以上都无效，直接运行启动脚本内置Python：
# 双击 run_nvidia_gpu.bat 启动ComfyUI，在弹出的黑窗口输入：
```bash
pip install requests>=2.30.0
```
# 自愿捐赠支持
本插件完全免费开源，无任何功能锁定、付费门槛，所有人均可无限制下载、使用、二次修改。
如果你觉得本节点大幅简化了 Agnes 视频 API 调用流程，节约了大量调试时间，愿意支持作者持续更新维护、优化功能、适配新版本接口，可以自愿捐赠。

所有捐赠仅作为创作激励，不强制、不捆绑任何功能权益，无论是否捐赠都能使用全部功能。
## 后续更新规划
1. 新增文生视频、多图关键帧动画双模式节点
2. 适配Agnes官方后续更新参数
3. 优化报错提示、增加参数预设模板
4. 兼容更多分辨率、帧率组合
### 捐赠渠道
作者：肖默
GitHub账号：suziran7991-hue
- 渠道：微信捐赠
![微信捐赠收款二维码](./donate_qrcode.png)
## 开源协议
本项目基于 MIT 开源协议，可自由分发、修改、商用，详情查看仓库根目录 `LICENSE` 文件
