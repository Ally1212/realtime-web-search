"""Generate ~100k unique search keywords, packed into 500-keyword campaigns (JSONL)."""
import argparse
import json
import sys


def block(items): return items.split()

# ---------- Chinese entities ----------
ZH_CITIES = block("北京 上海 广州 深圳 杭州 成都 重庆 武汉 西安 南京 苏州 天津 长沙 郑州 东莞 青岛 沈阳 宁波 昆明 大连 厦门 合肥 佛山 福州 哈尔滨 济南 温州 南宁 长春 泉州 石家庄 贵阳 南昌 金华 珠海 惠州 嘉兴 太原 徐州 南通 无锡 常州 烟台 唐山 洛阳 保定 兰州 海口 三亚 乌鲁木齐 拉萨 银川 西宁 呼和浩特 桂林 柳州 绵阳 宜宾 遵义 芜湖 株洲 湘潭 衡阳 岳阳 常德 襄阳 宜昌 九江 赣州 临沂 潍坊 淄博 威海 济宁 泰安 汕头 湛江 江门 中山 肇庆 绍兴 台州 扬州 镇江 泰州 盐城 淮安 连云港 宿迁 芜湖 马鞍山 安庆 滁州 阜阳 蚌埠 淮南 六安 莆田 漳州 龙岩 三明 南平 宁德 洛阳 开封 新乡 焦作 许昌 平顶山 安阳 商丘 信阳 周口 驻马店 南阳 大庆 齐齐哈尔 吉林 鞍山 抚顺 锦州 营口 盘锦 惠州 绵阳 德阳 南充 泸州 乐山 自贡 攀枝花 六盘水 曲靖 玉溪 大理 丽江 遵义 安顺 毕节 铜仁")
ZH_CITY_TOPICS = block("房价 天气 招聘 美食 旅游 新闻 地铁 大学 医院 限行 公积金 中考 高考 马拉松 演唱会 二手房 租房 落户 工资 特产")
ZH_BRANDS = block("华为 小米 苹果 三星 OPPO vivo 荣耀 联想 戴尔 惠普 华硕 索尼 佳能 尼康 海尔 美的 格力 TCL 海信 创维 比亚迪 特斯拉 蔚来 理想 小鹏 吉利 长城 长安 奇瑞 红旗 奔驰 宝马 奥迪 大众 丰田 本田 日产 福特 别克 宁德时代 茅台 五粮液 伊利 蒙牛 农夫山泉 李宁 安踏 耐克 阿迪达斯 优衣库 海底捞 星巴克 瑞幸 蜜雪冰城 肯德基 麦当劳 腾讯 阿里巴巴 字节跳动 百度 京东 拼多多 美团 滴滴 网易 快手 携程 顺丰 中兴 大疆 京东方 中芯国际 海康威视 科大讯飞 商汤 寒武纪 浪潮 用友 金蝶 三一重工 徐工 万科 保利 龙湖 招商蛇口 华润置地 中海地产 绿城 中国人寿 中国平安 中国太保 工商银行 建设银行 农业银行 中国银行 招商银行 交通银行 邮储银行 兴业银行 浦发银行 中信银行 民生银行 光大银行 蚂蚁集团 微众银行 强生 宝洁 联合利华 欧莱雅 雅诗兰黛 资生堂 百威 可口可乐 百事 雀巢 达能 沃尔玛 家乐福  Costco 宜家 迪士尼 环球影城 奈飞 波音 空客 空客中国 商飞 中移动 联通 电信 广电 顺丰 中通 圆通 韵达 申通 极兔 菜鸟 京东物流")
ZH_BRAND_TOPICS = block("最新消息 新闻 财报 新品 发布会 股价 招聘 降价 评测 合作 市值 裁员 投诉 售后 门店")
ZH_GAMES = block("王者荣耀 和平精英 原神 崩坏星穹铁道 绝区零 英雄联盟 英雄联盟手游 穿越火线 DOTA2 CS2 无畏契约 永劫无间 逆水寒 梦幻西游 大话西游 阴阳师 明日方舟 第五人格 光遇 蛋仔派对 元梦之星 金铲铲之战 云顶之弈 炉石传说 魔兽世界 守望先锋 暗黑破坏神 塞尔达传说 黑神话悟空 艾尔登法环 只狼 战神 最终幻想 怪物猎人 宝可梦 马里奥赛车 动物森友会 我的世界 泰拉瑞亚 饥荒 星露谷物语 文明6 三国志战略版 率土之滨 剑网3 天涯明月刀 一梦江湖 诛仙世界 幻塔 鸣潮 恋与深空 光与夜之恋 闪耀暖暖 FIFA 实况足球 NBA2K GTA6 使命召唤 战地风云 Apex英雄 绝地求生 堡垒之夜 双人成行 霍格沃茨之遗 博德之门3 赛博朋克2077 巫师3 上古卷轴6 辐射4 生化危机4 鬼泣5 刺客信条幻景 孤岛惊魂6 看门狗 全境封锁2 命运2 星际争霸2 帝国时代4 植物大战僵尸 开心消消乐 斗地主 麻将 象棋 围棋 五子棋 三国杀 狼人杀 剧本杀 密室逃脱 桌游")
ZH_GAME_TOPICS = block("攻略 更新 新赛季 新角色 新皮肤 兑换码 赛事 直播 下载 配置要求 剧情 评测")
ZH_HEALTH = block("感冒 发烧 咳嗽 头痛 失眠 焦虑 抑郁症 高血压 糖尿病 冠心病 心律失常 心衰 哮喘 慢阻肺 肺炎 支气管炎 鼻炎 咽炎 胃炎 胃溃疡 肠炎 便秘 腹泻 痔疮 肝炎 脂肪肝 肝硬化 肾炎 肾结石 尿路感染 前列腺炎 甲状腺结节 甲亢 甲减 乳腺增生 乳腺癌 肺癌 胃癌 肠癌 肝癌 胰腺癌 食道癌 宫颈癌 卵巢癌 前列腺癌 白血病 淋巴瘤 骨质疏松 关节炎 类风湿 痛风 腰椎间盘突出 颈椎病 肩周炎 骨折 湿疹 荨麻疹 痤疮 银屑病 白癜风 脱发 近视 白内障 青光眼 干眼症 中耳炎 扁桃体炎 口腔溃疡 牙周炎 龋齿 贫血 癫痫 帕金森 阿尔茨海默病 脑卒中 偏头痛 多动症 自闭症 强迫症 过敏 肥胖症 营养不良 水痘 麻疹 手足口病 流感 新冠 乙肝 丙肝 艾滋病 结核病 疟疾 登革热 狂犬病 破伤风 带状疱疹 麻疹 腮腺炎 猩红热")
ZH_HEALTH_TOPICS = block("症状 治疗 预防 吃什么药 医院 挂号 检查 费用 疫苗 康复")
ZH_FOODS = block("火锅 烧烤 奶茶 咖啡 麻辣烫 小龙虾 螺蛳粉 酸菜鱼 水煮鱼 宫保鸡丁 麻婆豆腐 红烧肉 糖醋排骨 鱼香肉丝 回锅肉 东坡肉 北京烤鸭 白切鸡 叫花鸡 佛跳墙 西湖醋鱼 龙井虾仁 松鼠桂鱼 剁椒鱼头 毛血旺 夫妻肺片 担担面 热干面 兰州拉面 重庆小面 炸酱面 刀削面 油泼面 烩面 云吞面 肠粉 煲仔饭 叉烧 烧鹅 白灼虾 清蒸鱼 蒜蓉粉丝蒸扇贝 椒盐排骨 可乐鸡翅 红烧狮子头 四喜丸子 锅包肉 地三鲜 木须肉 京酱肉丝 酱牛肉 卤味 凉拌菜 泡菜 寿司 刺身 拉面 天妇罗 烤肉 石锅拌饭 部队锅 炸鸡 披萨 汉堡 牛排 意面 沙拉 三明治 甜甜圈 蛋糕 面包 饼干 巧克力 冰淇淋 月饼 粽子 汤圆 饺子 包子 馒头 花卷 油条 豆浆 豆腐脑 煎饼果子 肉夹馍 羊肉泡馍 凉皮 擀面皮 米皮 胡辣汤 鸭血粉丝汤 生煎包 小笼包 蟹黄汤包 烧麦 春卷 糍粑 年糕")
ZH_FOOD_TOPICS = block("做法 配方 热量 加盟 外卖 推荐 测评 价格 营养 哪家好吃")
ZH_CARS = block("比亚迪秦 比亚迪汉 比亚迪唐 比亚迪宋 比亚迪元 比亚迪海豚 比亚迪海豹 特斯拉Model3 特斯拉ModelY 蔚来ET5 蔚来ES6 理想L7 理想L8 理想L9 小鹏P7 小鹏G6 问界M5 问界M7 问界M9 极氪001 极氪007 深蓝SL03 零跑C11 哪吒V 五菱宏光MINI 宏光MINIEV 长安CS75 哈弗H6 吉利星越L 博越 缤越 领克03 坦克300 坦克500 凯美瑞 雅阁 天籁 帕萨特 迈腾 速腾 朗逸 轩逸 卡罗拉 雷凌 思域 飞度 CR-V 皓影 RAV4 汉兰达 普拉多 宝马3系 宝马5系 宝马X3 奔驰C级 奔驰E级 奔驰GLC 奥迪A4L 奥迪A6L 奥迪Q5L 保时捷911 保时捷卡宴 路虎揽胜 捷豹XEL 沃尔沃XC60 凯迪拉克CT5 别克君威 福特蒙迪欧 现代伊兰特 起亚K5 马自达3 斯巴鲁森林人")
ZH_CAR_TOPICS = block("价格 油耗 续航 评测 优惠 落地价 保养 故障 召回 销量")
ZH_TECH = block("手机 笔记本 平板 耳机 手表 相机 无人机 投影仪 打印机 路由器 显示器 键盘 鼠标 音箱 充电宝 移动硬盘 U盘 存储卡 显卡 CPU 内存 主板 电源 机箱 散热器 固态硬盘 机械硬盘 网卡 摄像头 麦克风 扫地机器人 洗地机 空气炸锅 破壁机 榨汁机 咖啡机 电饭煲 电压力锅 微波炉 烤箱 蒸箱 洗碗机 消毒柜 冰箱 洗衣机 烘干机 空调 新风 空气净化器 加湿器 除湿机 电暖器 电风扇 热水器 净水器 软水机 吸尘器 电动牙刷 剃须刀 吹风机 卷发棒 美容仪 按摩椅 筋膜枪 跑步机 动感单车 划船机 椭圆机 智能门锁 智能猫眼 监控摄像头 智能音箱 智能电视 机顶盒 电子书 点读笔 翻译笔 学习机 电话手表")
ZH_TECH_TOPICS = block("推荐 评测 排行榜 哪个牌子好 价格 参数 对比 新品 拆解 维修")
ZH_FINANCE = block("股票 基金 债券 期货 期权 外汇 黄金 白银 原油 比特币 以太坊 狗狗币 莱特币 瑞波币 银行理财 大额存单 国债 逆回购 货币基金 指数基金 ETF REITs 可转债 新股 次新股 科创板 创业板 北交所 港股 美股 中概股 日经 纳斯达克 标普500 道琼斯 恒生指数 上证指数 深证成指 创业板指 沪深300 中证500 上证50 社保 医保 公积金 养老保险 失业保险 工伤保险 生育保险 企业年金 职业年金 个人所得税 增值税 消费税 关税 房产税 契税 印花税 车船税 遗产税")
ZH_FIN_TOPICS = block("行情 走势 分析 新闻 政策 怎么买 手续费 开户 风险 收益")
ZH_EDU = block("考研 高考 中考 专升本 自考 成人高考 公务员考试 事业编 教师招聘 教师资格证 会计初级 注册会计师 司法考试 执业药师 执业医师 护士资格证 一级建造师 二级建造师 消防工程师 造价工程师 监理工程师 安全工程师 雅思 托福 GRE GMAT SAT 四六级 专四专八 普通话 计算机二级 驾照 留学申请 奖学金 助学贷款 在职研究生 MBA EMBA 博士 博士后 幼儿园 小学 初中 高中 大学 职业教育 技工 培训 网课 家教 辅导班 托管 国际学校 私立学校 公办学校 学区房 择校 分班 军训 开学 毕业典礼 毕业论文 答辩 实习 校招 社招 简历 面试 试用期 转正 离职 跳槽 裁员 赔偿 劳动仲裁 五险一金")
ZH_EDU_TOPICS = block("报名时间 考试时间 成绩查询 分数线 政策 真题 备考 经验 费用 流程")
ZH_SPORTS = block("足球 篮球 排球 乒乓球 羽毛球 网球 高尔夫 棒球 橄榄球 冰球 台球 拳击 格斗 摔跤 柔道 跆拳道 空手道 游泳 跳水 体操 田径 马拉松 自行车 赛车 滑雪 滑冰 滑板 冲浪 攀岩 登山 潜水 帆船 皮划艇 赛艇 射箭 射击 击剑 马术 举重 健美 瑜伽 普拉提 广场舞 太极拳 武术 散打 泰拳 健身 减脂 增肌 跑步 徒步 露营 钓鱼 骑行 滑翔伞 蹦极 跳伞 漂流 溯溪 越野 斯巴达 铁人三项 中超 英超 西甲 意甲 德甲 法甲 欧冠 亚冠 NBA CBA WNBA NFL MLB NHL 世界杯 欧洲杯 亚洲杯 奥运会 亚运会 全运会 大运会 世锦赛 大满贯")
ZH_SPORT_TOPICS = block("赛程 比分 直播 转会 排名 新闻 门票 规则 装备 教学")
ZH_ENTERTAIN = block("电影 电视剧 综艺 动漫 纪录片 音乐剧 话剧 演唱会 音乐节 脱口秀 相声 小品 魔术 杂技 马戏 戏曲 京剧 越剧 黄梅戏 评剧 豫剧 川剧 粤剧 秦腔 昆曲 明星 演员 歌手 导演 编剧 制片人 网红 主播 博主 UP主 偶像 男团 女团 选秀 粉丝 应援 塌房 恋情 结婚 离婚 出轨 怀孕 生子 复出 退圈 封杀 代言 红毯 颁奖礼 奥斯卡 金鸡奖 百花奖 华表奖 金马奖 金像奖 戛纳 威尼斯 柏林 电影节 票房 收视率 口碑 评分 续集 翻拍 定档 撤档 上映 首映 路演 预告片 海报 主题曲 插曲 片尾曲 原声带")
ZH_ENT_TOPICS = block("最新消息 新闻 什么时候 哪里看 在线观看 下载 免费 完整版 剧情 大结局")
ZH_TRAVEL = block("三亚 丽江 大理 西双版纳 张家界 九寨沟 黄山 泰山 华山 庐山 峨眉山 长白山 桂林阳朔 鼓浪屿 乌镇 西塘 周庄 同里 凤凰古城 平遥古城 丽江古城 敦煌 莫高窟 兵马俑 故宫 长城 颐和园 天坛 西湖 拙政园 狮子林 外滩 东方明珠 迪士尼 环球影城 欢乐谷 长隆 方特 海昌 融创乐园 泰国 日本 韩国 新加坡 马来西亚 越南 柬埔寨 缅甸 老挝 印尼 菲律宾 马尔代夫 迪拜 土耳其 埃及 法国 意大利 瑞士 英国 德国 西班牙 葡萄牙 希腊 奥地利 荷兰 比利时 捷克 美国 加拿大 墨西哥 巴西 阿根廷 澳大利亚 新西兰 俄罗斯 冰岛 挪威 芬兰 瑞典 丹麦")
ZH_TRAVEL_TOPICS = block("旅游攻略 自由行 跟团 签证 机票 酒店 景点 美食 购物 花费")

# ---------- English entities ----------
EN_CITIES = block("NewYork LosAngeles Chicago Houston Phoenix Philadelphia SanAntonio SanDiego Dallas SanJose Austin Jacksonville FortWorth Columbus Charlotte SanFrancisco Indianapolis Seattle Denver Washington Boston Nashville Portland LasVegas Memphis Louisville Baltimore Milwaukee Albuquerque Tucson Fresno Sacramento Atlanta Miami Oakland Minneapolis Cleveland Detroit Baltimore London Paris Berlin Madrid Rome Amsterdam Vienna Brussels Zurich Geneva Stockholm Oslo Copenhagen Helsinki Dublin Lisbon Prague Warsaw Budapest Athens Tokyo Osaka Seoul Beijing Shanghai HongKong Singapore Bangkok Dubai Mumbai Delhi Sydney Melbourne Toronto Vancouver Montreal")
EN_CITY_TOPICS = block("news weather jobs housing restaurants events realestate traffic schools crime")
EN_BRANDS = block("Apple Microsoft Google Amazon Meta Tesla Nvidia AMD Intel Qualcomm Broadcom Oracle Salesforce Adobe IBM Cisco Samsung Sony LG Panasonic Philips Bosch Siemens GE Honeywell 3M Caterpillar JohnDeere Boeing Airbus Lockheed Raytheon SpaceX BlueOrigin OpenAI Anthropic Netflix Disney Warner Paramount Spotify Uber Lyft Airbnb DoorDash Shopify Square PayPal Visa Mastercard JPMorgan GoldmanSachs MorganStanley BankOfAmerica WellsFargo Citigroup BlackRock Walmart Target Costco HomeDepot Lowes Nike Adidas Lululemon Starbucks McDonalds Chipotle Yum CocaCola PepsiCo Nestle Unilever ProcterGamble JohnsonJohnson Pfizer Moderna Merck AbbVie EliLilly Novartis Roche AstraZeneca GSK Sanofi Bayer Novo Ford GM Stellantis Toyota Honda Volkswagen BMW Mercedes Porsche Ferrari Lamborghini Rivian Lucid NIO XPeng LiAuto Exxon Chevron Shell BP TotalEnergies")
EN_BRAND_TOPICS = block("news stock earnings layoffs launch review recall lawsuit deal acquisition")
EN_GAMES = block("Fortnite Minecraft Roblox GTA5 GTA6 CallOfDuty Warzone ApexLegends Valorant LeagueOfLegends Dota2 CounterStrike Overwatch WorldOfWarcraft Diablo EldenRing BaldursGate3 Cyberpunk2077 Witcher3 Skyrim Fallout Starfield Zelda MarioKart SuperSmashBros PokemonGo PokemonScarlet AnimalCrossing StardewValley Terraria AmongUs FallGuys RocketLeague FIFA24 EAFC Madden NBA2K MLBTheShow GranTurismo ForzaHorizon NeedForSpeed AssassinsCreed FarCry WatchDogs TheDivision Destiny2 Halo GodOfWar HorizonForbiddenWest GhostOfTsushima SpiderMan2 TheLastOfUs Uncharted GodOfWarRagnarok FinalFantasy16 ResidentEvil4 DeadSpace SilentHill2 MetalGearSolid DeathStranding Cyberpunk PhantomLiberty Palworld Helldivers2 BlackMythWukong")
EN_GAME_TOPICS = block("update patch review gameplay trailer release guide tips reddit leaks")
EN_FIN = block("S&P500 Nasdaq DowJones Russell2000 Bitcoin Ethereum Solana Cardano XRP Dogecoin FedRate Inflation CPI GDP UnemploymentJobs TreasuryBonds GoldPrice OilPrice SilverPrice NaturalGas CopperPrice WheatPrice CornPrice Forex EURUSD GBPUSD USDJPY Stocks Options Futures ETFs MutualFunds IPO SPAC Dividends Buybacks MortgageRates CreditCards PersonalLoans StudentLoans AutoLoans HomeEquity Retirement401k IRA RothIRA SocialSecurity Medicare Medicaid")
EN_FIN_TOPICS = block("news today analysis forecast prediction outlook report update live")
EN_HEALTH = block("Flu COVID RSV Measles Mpox BirdFlu Diabetes Cancer HeartDisease Stroke Alzheimer Parkinson Depression Anxiety ADHD Autism OCD PTSD Bipolar Schizophrenia Obesity Asthma COPD Arthritis Osteoporosis Migraine Epilepsy MultipleSclerosis Lupus Crohns Celiac IBS KidneyDisease LiverDisease Hepatitis HIV Tuberculosis Malaria Dengue Lyme Pneumonia Bronchitis Sinusitis Eczema Psoriasis Acne HairLoss Insomnia SleepApnea Fibromyalgia ChronicFatigue Endometriosis PCOS Menopause Infertility Pregnancy BreastCancer LungCancer ColonCancer ProstateCancer SkinCancer Leukemia Lymphoma")
EN_HEALTH_TOPICS = block("symptoms treatment vaccine research study outbreak news update guidelines")
EN_TECH = block("iPhone16 iPhone17 GalaxyS25 Pixel9 MacBook AirPods VisionPro AppleWatch iPad Windows12 ChatGPT GPT5 Claude Gemini Copilot Midjourney StableDiffusion Sora DALLE Android16 iOS19 macOS Linux Ubuntu Docker Kubernetes AWS Azure GCP Cloudflare NvidiaRTX IntelCore AMDRyzen Ryzen9000 Snapdragon Apple Silicon M4 WiFi7 5G 6G Starlink QuantumComputing Blockchain Web3 NFT Metaverse VR AR SmartHome RobotVacuum ElectricVehicle AutonomousDriving DroneDelivery HumanoidRobot")
EN_TECH_TOPICS = block("news review release specs price comparison launch rumors leaks")
EN_SPORTS = block("NFL NBA MLB NHL MLS PremierLeague LaLiga SerieA Bundesliga Ligue1 ChampionsLeague EuropaLeague WorldCup Olympics F1 NASCAR IndyCar MotoGP UFC Boxing WWE Tennis GrandSlam Golf PGA Masters USOpen Wimbledon FrenchOpen AustralianOpen Cricket Rugby Cycling TourDeFrance Marathon Triathlon Swimming Gymnastics FigureSkating Skiing Snowboarding Surfing Skateboarding Climbing")
EN_SPORT_TOPICS = block("scores schedule standings news rumors trade draft injury highlights")
EN_ENT = block("Marvel DC StarWars HarryPotter LordOfTheRings GameOfThrones HouseOfDragon StrangerThings Wednesday SquidGame TheLastOfUs Fallout TheWitcher Bridgerton Yellowstone Succession WhiteLotus Euphoria TedLasso Severance TheBear Shogun Dune Oppenheimer Barbie Avatar TaylorSwift Beyonce Drake KendrickLamar BillieEilish OliviaRodrigo SabrinaCarpenter ChappellRoan BadBunny TheWeeknd ArianaGrande EdSheeran Adele BTS Blackpink KanyeWest KimKardashian Oscars Grammy Emmys GoldenGlobes CannesFilmFestival ComicCon Netflix DisneyPlus HBOMax PrimeVideo Hulu AppleTV")
EN_ENT_TOPICS = block("news release trailer cast review spoilers premiere date season episode")
EN_TRAVEL = block("Japan Thailand Italy France Spain Greece Portugal Iceland Norway Switzerland Bali Maldives Hawaii Cancun Dubai Singapore Korea Vietnam Philippines Australia NewZealand CostaRica Peru Iceland Norway Finland Morocco Egypt Jordan Turkey Croatia CzechRepublic Austria Netherlands Belgium Ireland Scotland")
EN_TRAVEL_TOPICS = block("travelguide itinerary visa flights hotels attractions food budget tips")
EN_JOBS = block("SoftwareEngineer DataScientist ProductManager Designer Marketing Sales Accountant Nurse Teacher Lawyer Doctor Engineer Electrician Plumber Welder Mechanic Carpenter Chef Waiter Bartender Barista Driver Pilot FlightAttendant Police Firefighter Soldier RealEstate Insurance FinancialAnalyst Consultant Analyst Manager Director Executive Freelancer Remote Hybrid")
EN_JOB_TOPICS = block("jobs salary hiring interview resume career remote layoffs trends")

ZH_POOLS = [
    (ZH_CITIES, ZH_CITY_TOPICS), (ZH_BRANDS, ZH_BRAND_TOPICS), (ZH_GAMES, ZH_GAME_TOPICS),
    (ZH_HEALTH, ZH_HEALTH_TOPICS), (ZH_FOODS, ZH_FOOD_TOPICS), (ZH_CARS, ZH_CAR_TOPICS),
    (ZH_TECH, ZH_TECH_TOPICS), (ZH_FINANCE, ZH_FIN_TOPICS), (ZH_EDU, ZH_EDU_TOPICS),
    (ZH_SPORTS, ZH_SPORT_TOPICS), (ZH_ENTERTAIN, ZH_ENT_TOPICS), (ZH_TRAVEL, ZH_TRAVEL_TOPICS),
]
EN_POOLS = [
    (EN_CITIES, EN_CITY_TOPICS), (EN_BRANDS, EN_BRAND_TOPICS), (EN_GAMES, EN_GAME_TOPICS),
    (EN_FIN, EN_FIN_TOPICS), (EN_HEALTH, EN_HEALTH_TOPICS), (EN_TECH, EN_TECH_TOPICS),
    (EN_SPORTS, EN_SPORT_TOPICS), (EN_ENT, EN_ENT_TOPICS), (EN_TRAVEL, EN_TRAVEL_TOPICS),
    (EN_JOBS, EN_JOB_TOPICS),
]

def gen(pools, extra_intents):
    seen, out = set(), []
    for entities, topics in pools:
        for e in entities:
            out.append(e)
            for t in topics:
                for intent in extra_intents:
                    kw = f"{e} {t}{intent}".strip()
                    if kw not in seen:
                        seen.add(kw); out.append(kw)
    return out

zh = gen(ZH_POOLS, ["", " 2026", " 最新", " 新闻"])
en = gen(EN_POOLS, ["", " 2026", " latest", " news"])
print("zh:", len(zh), "en:", len(en), "total:", len(zh)+len(en), file=sys.stderr)

def pack(words, lang, start):
    size = 500
    rows = []
    for i in range(0, len(words), size):
        chunk = words[i:i+size]
        rows.append({"query": chunk[0], "aliases": chunk[1:], "proxy_profile": "private",
                     "sources": ["google_web"], "name": f"mass-{lang}-{start+len(rows)+1:03d}"})
    return rows

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", help="output JSONL path for campaign records")
args = parser.parse_args()

rows = pack(zh, "zh", 0) + pack(en, "en", 0)
with open(args.output, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print("campaigns:", len(rows), file=sys.stderr)
