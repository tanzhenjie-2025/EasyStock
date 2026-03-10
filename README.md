EasyStock/                  # 项目根目录（整个EasyStock工程的容器）
├── EasyStock/              # 主项目配置目录（Django核心配置层）
│   ├── __init__.py         # 空文件，标识该目录为Python包
│   ├── asgi.py             # ASGI协议配置（生产环境异步部署用，如Daphne/uvicorn）
│   ├── settings.py         # 全局配置文件（核心！应用注册、数据库、静态文件等）
│   ├── urls.py             # 项目总路由（所有请求的入口，挂载各应用的URL）
│   └── wsgi.py             # WSGI协议配置（生产环境部署用，如Gunicorn/Apache）
├── bill/                   # 核心业务应用（开单+库存管理，整个项目的核心模块）
│   ├── __init__.py         # 标识为Python包
│   ├── admin.py            # Django Admin后台配置（管理商品、订单数据）
│   ├── apps.py             # 应用元配置（注册bill应用，定义应用名称等）
│   ├── migrations/         # 数据库迁移文件（自动生成，记录模型结构变更）
│   │   └── __init__.py
│   ├── models.py           # 数据模型（商品、订单、订单明细，核心数据结构）
│   ├── tests.py            # 单元测试文件（可编写功能测试用例，保障代码稳定性）
│   ├── urls.py             # 应用级路由（映射开单、检索、打印等接口）
│   └── views.py            # 视图函数（处理业务逻辑：拼音检索、保存订单、打印等）
├── static/                 # 静态文件目录（JS、CSS、第三方库，前端资源）
│   └── js-pinyin.min.js    # 前端拼音辅助库（兜底拼音检索，兼容前端离线场景）
├── templates/              # 模板文件目录（HTML页面，按应用分目录更规范）
│   └── bill/               # bill应用专属模板（避免多应用模板冲突）
│       ├── index.html      # 开单主页面（拼音检索、三联单填写、自动换行/切页）
│       ├── print.html      # 三联单打印页面（自定义打印样式，适配三联单打印机）
│       ├── stock.html      # 库存查询页面（展示所有商品的库存状态）
│       └── order_list.html # 订单记录页面（查看/打印历史开单记录）
├── manage.py               # Django命令行工具（项目核心操作入口）
├── requirements.txt        # 项目依赖清单（一键安装所有依赖，便于部署）
└── README.md               # 项目说明文档（使用/部署/扩展指南）