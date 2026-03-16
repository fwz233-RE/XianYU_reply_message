## 🌟 本项目基于开源项目修改而来，以下为原项目README.MD，如需联系我请发邮件fwz233@qq.com。联系原项目相关人员参考以下的介绍

# ---------------------------------------

# 🚀 Xianyu AutoAgent - 智能闲鱼客服机器人系统

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/) [![LLM Powered](https://img.shields.io/badge/LLM-powered-FF6F61)](https://platform.openai.com/)

专为闲鱼平台打造的AI值守解决方案，实现闲鱼平台7×24小时自动化值守，支持多专家协同决策、智能议价和上下文感知对话。 


## 🌟 核心特性

### 智能对话引擎
| 功能模块   | 技术实现            | 关键特性                                                     |
| ---------- | ------------------- | ------------------------------------------------------------ |
| 上下文感知 | 会话历史存储        | 轻量级对话记忆管理，完整对话历史作为LLM上下文输入            |
| 专家路由   | LLM prompt+规则路由 | 基于提示工程的意图识别 → 专家Agent动态分发，支持议价/技术/客服多场景切换 |

### 业务功能矩阵
| 模块     | 已实现                        | 规划中                       |
| -------- | ----------------------------- | ---------------------------- |
| 核心引擎 | ✅ LLM自动回复<br>✅ 上下文管理 | 🔄 情感分析增强               |
| 议价系统 | ✅ 阶梯降价策略                | 🔄 市场比价功能               |
| 技术支持 | ✅ 网络搜索整合                | 🔄 RAG知识库增强              |
| 运维监控 | ✅ 基础日志                    | 🔄 钉钉集成<br>🔄  Web管理界面 |

## 🎨效果图
<div align="center">
  <img src="./images/demo1.png" width="600" alt="客服">
  <br>
  <em>图1: 客服随叫随到</em>
</div>


<div align="center">
  <img src="./images/demo2.png" width="600" alt="议价专家">
  <br>
  <em>图2: 阶梯式议价</em>
</div>

<div align="center">
  <img src="./images/demo3.png" width="600" alt="技术专家"> 
  <br>
  <em>图3: 技术专家上场</em>
</div>

<div align="center">
  <img src="./images/log.png" width="600" alt="后台log"> 
  <br>
  <em>图4: 后台log</em>
</div>


## 🚴 快速开始
小白请直接查看[保姆级教学文档](https://my.feishu.cn/wiki/JtkBwkI9GiokZikVdyNceEfZncE)
### 环境要求
- Python 3.8+

### 安装步骤
```bash
1. 克隆仓库
git clone https://github.com/shaxiu/XianyuAutoAgent.git
cd XianyuAutoAgent

2. 安装依赖
pip install -r requirements.txt

3. 配置环境变量
创建一个 `.env` 文件，包含以下内容，也可直接重命名 `.env.example` ：
#必配配置
API_KEY=apikey通过模型平台获取
COOKIES_STR=填写网页端获取的cookie
MODEL_BASE_URL=模型地址
MODEL_NAME=模型名称
#可选配置
TOGGLE_KEYWORDS=接管模式切换关键词，默认为句号（输入句号切换为人工接管，再次输入则切换AI接管）
SIMULATE_HUMAN_TYPING=True/False #模拟人工回复延迟
COOKIE_SOURCE=env/plugin # env=手动Cookie，plugin=浏览器插件自动推送
COOKIE_BRIDGE_TOKEN=change_me_cookie_bridge_token # 插件与Python桥接鉴权token（plugin模式建议设置）
COOKIE_PROJECT_ID=my_shop_a # 多项目隔离标识，建议每个实例唯一
COOKIE_ACCOUNT_HINT=1234567890 # 可选，限制仅接收指定闲鱼账号

注意：默认使用的模型是通义千问，如需使用其他API，请自行修改.env文件中的模型地址和模型名称；
COOKIES_STR自行在闲鱼网页端获取cookies(网页端F12打开控制台，选择Network，点击Fetch/XHR,点击一个请求，查看cookies)

4. 创建提示词文件prompts/*_prompt.txt（也可以直接将模板名称中的_example去掉），否则默认读取四个提示词模板中的内容
```

### 使用方法

运行主程序：
```bash
python main.py
```

### Web UI 启动（IM风格）

支持本地 IM 风格界面，包含：
- 多会话列表与会话切换
- 聊天窗口实时刷新（SSE）
- 手动发送消息
- 会话人工/自动模式切换
- 常用语编辑与一键发送
- 运行状态显示（WebSocket、心跳、Token刷新、模型状态、Cookie等待状态）

启动方式：
```bash
python app_ui.py
```

或在项目根目录使用：
```bash
start_xianyu_ui.bat
```

### Edge / Chrome 双实例启动

如果你想同时挂两个闲鱼账号，项目现在支持直接把 Edge 和 Chrome 分成两个独立实例运行：

```bash
start_xianyu_edge.bat
start_xianyu_chrome.bat
start_xianyu_dual.bat
```

说明：
- `start_xianyu_edge.bat`：使用 `.env.edge`、`data/edge`、`data/edge_profile`、`UI_PORT=8765`、`COOKIE_BRIDGE_PORT=18765`
- `start_xianyu_chrome.bat`：使用 `.env.chrome`、`data/chrome`、`data/chrome_profile`、`UI_PORT=8766`、`COOKIE_BRIDGE_PORT=18766`
- `start_xianyu_dual.bat`：同时拉起两个实例
- 两个实例的聊天数据库、规则文件、Cookie桥接端口、浏览器 Profile 都相互隔离，不会互相覆盖 Cookie

默认访问地址：
`http://127.0.0.1:8765`

可通过环境变量调整：
```bash
UI_HOST=127.0.0.1
UI_PORT=8765
```

### 浏览器插件自动获取 Cookie（推荐 anti-bot 场景）

当你不希望每次手动复制 Cookie 时，可以启用 `COOKIE_SOURCE=plugin` 模式。  
该模式不会驱动自动化浏览器，而是使用你手动操作的真实浏览器，通过插件将最终 Cookie 推送到本机 Python 进程。

#### 1) 启用插件模式
在 `.env` 中设置：
```bash
COOKIE_SOURCE=plugin
COOKIE_BRIDGE_HOST=127.0.0.1
COOKIE_BRIDGE_PORT=18765
COOKIE_BRIDGE_TOKEN=自行设置一个随机字符串
COOKIE_PROJECT_ID=my_shop_a
COOKIE_ACCOUNT_HINT=你的闲鱼unb（可选）
COOKIE_WAIT_TIMEOUT=300
COOKIE_REQUIRE_CHANGE=True
```

注意：若使用 Web UI，推荐同样开启 `COOKIE_SOURCE=plugin`，这样风控后会在 UI 状态区显示“等待Cookie刷新”，完成滑块后自动恢复。

#### 2) 安装浏览器插件（Chrome/Edge）
插件目录在仓库根目录外层：
- 通用版：`../browser_cookie_bridge`
- Edge 双实例专用：`../browser_cookie_bridge/edge`
- Chrome 双实例专用：`../browser_cookie_bridge/chrome`

步骤：
1. 打开浏览器扩展管理页，启用「开发者模式」
2. 选择「加载已解压的扩展程序」
3. 单实例模式选择 `browser_cookie_bridge` 文件夹；双实例模式请分别选择 `browser_cookie_bridge/edge` 和 `browser_cookie_bridge/chrome`
4. 打开插件弹窗，配置：
   - `Bridge URL`：`http://127.0.0.1:18765/cookie`
   - `Bridge Token`：与 `.env` 中 `COOKIE_BRIDGE_TOKEN` 一致
   - `Project ID`：与 `.env` 中 `COOKIE_PROJECT_ID` 一致
   - `Account Hint`：可选，建议填写该账号的 `unb`
   - 勾选「启用监听」

使用新启动脚本时，会自动带上对应浏览器 Profile 和对应扩展目录；首次双实例启动时，推荐直接使用：
- Edge：`start_xianyu_edge.bat`
- Chrome：`start_xianyu_chrome.bat`
- 双开：`start_xianyu_dual.bat`

#### 3) 运行流程（双阶段）
1. 启动 `python main.py`（会先等待插件 Cookie）
2. 打开闲鱼消息页并登录：`https://www.goofish.com/im?spm`
3. 首次Cookie被插件记录为“基线”，此时不会立即使用
4. 你手动完成滑块/验证后，Cookie发生变化
5. 插件推送变化后的 Cookie，Python 自动写入 `.env` 并继续启动

#### 3.1) 多账号 / 多项目同时运行
推荐采用“一个项目实例 + 一个浏览器 Profile + 一组独立桥接配置”的隔离方式：
1. 每个项目实例设置独立的 `COOKIE_BRIDGE_PORT`、`COOKIE_BRIDGE_TOKEN`、`COOKIE_PROJECT_ID`
2. 每个闲鱼账号使用独立浏览器 Profile 登录，避免 Cookie 相互覆盖
3. 插件中的 `Project ID` 与项目 `.env` 保持一致；如需更严格隔离，再填写 `Account Hint`
4. 本仓库已内置 `edge/chrome` 双实例默认配置：`.env.edge/.env.chrome`、`data/edge/data/chrome`、`18765/18766`、`8765/8766`

这样多个项目同时运行时，桥接服务只会接收发给自己的 Cookie，不会串号。

#### 4) 风控后自动恢复
当日志出现 `RGV587_ERROR` 时，程序会优先等待插件推送新 Cookie；  
如果超时/未收到，再回退到终端手动输入。

### 自定义提示词

可以通过编辑 `prompts` 目录下的文件来自定义各个专家的提示词：

- `classify_prompt.txt`: 意图分类提示词
- `price_prompt.txt`: 价格专家提示词
- `tech_prompt.txt`: 技术专家提示词
- `default_prompt.txt`: 默认回复提示词

## 🤝 参与贡献

欢迎通过 Issue 提交建议或 PR 贡献代码，请遵循 [贡献指南](https://contributing.md/)



## 🛡 注意事项

⚠️ 注意：**本项目仅供学习与交流，如有侵权联系作者删除。**

鉴于项目的特殊性，开发团队可能在任何时间**停止更新**或**删除项目**。

如需学习交流，请联系：[coderxiu@qq.com](https://mailto:coderxiu@qq.com/)

## 📱 交流群
欢迎加入项目交流群，交流技术、分享经验、互助学习。
<div align="center">
  <table>
    <tr>
      <td align="center"><strong>交流群17（已满200）</strong></td>
      <td align="center"><strong>交流群18（推荐加入）</strong></td>
    </tr>
    <tr>
      <td><img src="./images/wx_group17.png" width="300px" alt="交流群17"></td>
      <td><img src="./images/wx_group18.png" width="300px" alt="交流群18"></td>
    </tr>
  </table>
</div>

## 💼 寻找机会

### <a href="https://github.com/shaxiu">@Shaxiu</a>
**🔍寻求方向**：**AI产品经理**  
**📫 联系：** **email**:coderxiu@qq.com；**wx:** coderxiu

### <a href="https://github.com/cv-cat">@CVcat</a>
**🔍寻求方向**：**研发工程师**（python、java、逆向、爬虫）  
**📫 联系：** **email:** 992822653@qq.com；**wx:** CVZC15751076989
## ☕ 请喝咖啡
您的☕和⭐将助力项目持续更新：

<div align="center">
  <img src="./images/wechat_pay.jpg" width="400px" alt="微信赞赏码"> 
  <img src="./images/alipay.jpg" width="400px" alt="支付宝收款码">
</div>


## 📈 Star 趋势
<a href="https://www.star-history.com/#shaxiu/XianyuAutoAgent&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=shaxiu/XianyuAutoAgent&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=shaxiu/XianyuAutoAgent&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=shaxiu/XianyuAutoAgent&type=Date" />
 </picture>
</a>


