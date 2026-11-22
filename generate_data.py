"""从 football-data.co.uk 公开 CSV 生成 GitHub Pages 使用的真实联赛数据。"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEAGUES = [
    {"id": "E0", "name": "英超", "source_name": "England Premier League"},
    {"id": "SP1", "name": "西甲", "source_name": "Spain La Liga"},
    {"id": "I1", "name": "意甲", "source_name": "Italy Serie A"},
    {"id": "D1", "name": "德甲", "source_name": "Germany Bundesliga"},
    {"id": "F1", "name": "法甲", "source_name": "France Ligue 1"},
    {"id": "N1", "name": "荷甲", "source_name": "Netherlands Eredivisie"},
    {"id": "P1", "name": "葡超", "source_name": "Portugal Liga Portugal"},
    {"id": "B1", "name": "比甲", "source_name": "Belgium Pro League"},
    {"id": "SC0", "name": "苏超", "source_name": "Scottish Premiership"},
    {"id": "T1", "name": "土超", "source_name": "Turkey Super Lig"},
    {"id": "G1", "name": "希腊超", "source_name": "Greece Super League"},
    # 五大联赛次一级
    {"id": "E1", "name": "英冠", "source_name": "England Championship"},
    {"id": "SP2", "name": "西乙", "source_name": "Spain Segunda Division"},
    {"id": "I2", "name": "意乙", "source_name": "Italy Serie B"},
    {"id": "D2", "name": "德乙", "source_name": "Germany 2. Bundesliga"},
    {"id": "F2", "name": "法乙", "source_name": "France Ligue 2"},
    # 其他主流顶级联赛：new/*.csv 同时含历史赛季，必须按 Season 与 Country 过滤。
    {"id": "SWE", "name": "瑞典超", "source_name": "Sweden Allsvenskan", "format": "extra", "country": "Sweden", "season_type": "calendar"},
    {"id": "SWZ", "name": "瑞士超", "source_name": "Switzerland Super League", "format": "extra", "country": "Switzerland", "season_type": "cross_year"},
    {"id": "JPN", "name": "日本J1", "source_name": "Japan J1 League", "format": "extra", "country": "Japan", "season_type": "cross_year"},
    {"id": "AUT", "name": "奥超", "source_name": "Austria Bundesliga", "format": "extra", "country": "Austria", "season_type": "cross_year"},
    {"id": "RUS", "name": "俄超", "source_name": "Russia Premier League", "format": "extra", "country": "Russia", "season_type": "cross_year"},
    {"id": "NOR", "name": "挪超", "source_name": "Norway Eliteserien", "format": "extra", "country": "Norway", "season_type": "calendar"},
    {"id": "BRA", "name": "巴甲", "source_name": "Brazil Serie A", "format": "extra", "country": "Brazil", "season_type": "calendar"},
    {"id": "ARG", "name": "阿甲", "source_name": "Argentina Primera Division", "format": "extra", "country": "Argentina", "season_type": "calendar"},
    {"id": "POL", "name": "波超", "source_name": "Poland Ekstraklasa", "format": "extra", "country": "Poland", "season_type": "cross_year"},
    {"id": "DNK", "name": "丹超", "source_name": "Denmark Superliga", "format": "extra", "country": "Denmark", "season_type": "cross_year"},
    {"id": "FIN", "name": "芬超", "source_name": "Finland Veikkausliiga", "format": "extra", "country": "Finland", "season_type": "calendar"},
    {"id": "ROU", "name": "罗甲", "source_name": "Romania Liga 1", "format": "extra", "country": "Romania", "season_type": "cross_year"},
    {"id": "MEX", "name": "墨超", "source_name": "Mexico Liga MX", "format": "extra", "country": "Mexico", "season_type": "cross_year"},
    {"id": "USA", "name": "美职联", "source_name": "USA Major League Soccer", "format": "extra", "country": "USA", "season_type": "calendar"},
]
USER_AGENT = "poisson-football-lab/1.0 (+https://github.com/shanyan0820/poisson-football)"

# 数据源英文队名到页面中文队名。英文原名同时保留在每场数据里，便于回溯原始 CSV。
TEAM_ZH = {
    # 英超
    "Arsenal": "阿森纳", "Aston Villa": "阿斯顿维拉", "Bournemouth": "伯恩茅斯",
    "Brentford": "布伦特福德", "Brighton": "布莱顿", "Chelsea": "切尔西",
    "Coventry": "考文垂", "Crystal Palace": "水晶宫", "Everton": "埃弗顿",
    "Fulham": "富勒姆", "Hull": "赫尔城", "Ipswich": "伊普斯维奇",
    "Leeds": "利兹联", "Liverpool": "利物浦", "Man City": "曼城",
    "Man United": "曼联", "Newcastle": "纽卡斯尔联", "Nott'm Forest": "诺丁汉森林",
    "Sunderland": "桑德兰", "Tottenham": "托特纳姆热刺",
    # 西甲
    "Alaves": "阿拉维斯", "Ath Bilbao": "毕尔巴鄂竞技", "Ath Madrid": "马德里竞技",
    "Barcelona": "巴塞罗那", "Betis": "皇家贝蒂斯", "Celta": "塞尔塔",
    "Elche": "埃尔切", "Espanol": "西班牙人", "Getafe": "赫塔费",
    "La Coruna": "拉科鲁尼亚", "Levante": "莱万特", "Malaga": "马拉加",
    "Osasuna": "奥萨苏纳", "Real Madrid": "皇家马德里", "Santander": "桑坦德竞技",
    "Sevilla": "塞维利亚", "Sociedad": "皇家社会", "Valencia": "瓦伦西亚",
    "Vallecano": "巴列卡诺", "Villarreal": "比利亚雷亚尔",
    # 意甲
    "Atalanta": "亚特兰大", "Bologna": "博洛尼亚", "Cagliari": "卡利亚里",
    "Como": "科莫", "Fiorentina": "佛罗伦萨", "Frosinone": "弗罗西诺内",
    "Genoa": "热那亚", "Inter": "国际米兰", "Juventus": "尤文图斯",
    "Lazio": "拉齐奥", "Lecce": "莱切", "Milan": "AC米兰",
    "Monza": "蒙扎", "Napoli": "那不勒斯", "Parma": "帕尔马",
    "Roma": "罗马", "Sassuolo": "萨索洛", "Torino": "都灵",
    "Udinese": "乌迪内斯", "Venezia": "威尼斯",
    # 德甲
    "Augsburg": "奥格斯堡", "Bayern Munich": "拜仁慕尼黑", "Dortmund": "多特蒙德",
    "Ein Frankfurt": "法兰克福", "Elversberg": "埃弗斯堡", "FC Koln": "科隆",
    "Freiburg": "弗赖堡", "Hamburg": "汉堡", "Hoffenheim": "霍芬海姆",
    "Leverkusen": "勒沃库森", "M'gladbach": "门兴格拉德巴赫", "Mainz": "美因茨",
    "Paderborn": "帕德博恩", "RB Leipzig": "RB莱比锡", "Schalke 04": "沙尔克04",
    "Stuttgart": "斯图加特", "Union Berlin": "柏林联合", "Werder Bremen": "云达不莱梅",
    # 法甲
    "Angers": "昂热", "Auxerre": "欧塞尔", "Brest": "布雷斯特",
    "Le Havre": "勒阿弗尔", "Le Mans": "勒芒", "Lens": "朗斯",
    "Lille": "里尔", "Lorient": "洛里昂", "Lyon": "里昂",
    "Marseille": "马赛", "Monaco": "摩纳哥", "Nice": "尼斯",
    "Paris FC": "巴黎FC", "Paris SG": "巴黎圣日耳曼", "Rennes": "雷恩",
    "Strasbourg": "斯特拉斯堡", "Toulouse": "图卢兹", "Troyes": "特鲁瓦",
    # 荷甲
    "AZ Alkmaar": "阿尔克马尔", "Ajax": "阿贾克斯", "Cambuur": "坎布尔",
    "Den Haag": "海牙", "Excelsior": "鹿特丹精英", "Feyenoord": "费耶诺德",
    "For Sittard": "福图纳锡塔德", "Go Ahead Eagles": "前进之鹰", "Groningen": "格罗宁根",
    "Heerenveen": "海伦芬", "Nijmegen": "奈梅亨", "PSV Eindhoven": "PSV埃因霍温",
    "Sparta Rotterdam": "鹿特丹斯巴达", "Telstar": "特尔斯达", "Twente": "特温特",
    "Utrecht": "乌德勒支", "Willem II": "威廉二世", "Zwolle": "兹沃勒",
    # 葡超
    "Academico Viseu": "维塞乌学院", "Alverca": "阿尔韦卡", "Arouca": "阿罗卡",
    "Benfica": "本菲卡", "Casa Pia": "卡萨皮亚", "Estoril": "埃斯托里尔",
    "Estrela": "阿马多拉之星", "Famalicao": "法马利康", "Gil Vicente": "吉尔维森特",
    "Guimaraes": "吉马良斯", "Maritimo": "马里迪莫", "Moreirense": "莫雷拉人",
    "Nacional": "葡萄牙国民", "Porto": "波尔图", "Rio Ave": "里奥阿维",
    "Santa Clara": "圣克拉拉", "Sp Braga": "布拉加", "Sp Lisbon": "葡萄牙体育",
    # 比甲
    "Anderlecht": "安德莱赫特", "Antwerp": "安特卫普", "Beveren": "贝弗伦",
    "Cercle Brugge": "色格拉布鲁日", "Charleroi": "沙勒罗瓦", "Club Brugge": "布鲁日",
    "Genk": "亨克", "Gent": "根特", "Kortrijk": "科特赖克",
    "Lommel SK": "洛默尔", "Mechelen": "梅赫伦", "Oud-Heverlee Leuven": "鲁汶",
    "RAAL La Louviere": "拉卢维耶尔", "St Truiden": "圣图尔登", "St. Gilloise": "圣吉罗斯联合",
    "Standard": "标准列日", "Waregem": "聚尔特瓦雷赫姆", "Westerlo": "韦斯特洛",
    # 苏超
    "Aberdeen": "阿伯丁", "Celtic": "凯尔特人", "Dundee": "邓迪",
    "Dundee United": "邓迪联", "Falkirk": "福尔柯克", "Hearts": "哈茨",
    "Hibernian": "希伯尼安", "Kilmarnock": "基尔马诺克", "Motherwell": "马瑟韦尔",
    "Rangers": "格拉斯哥流浪者", "St Johnstone": "圣约翰斯通", "St Mirren": "圣米伦",
    # 土超
    "Alanyaspor": "阿兰亚体育", "Amedspor": "阿梅德体育", "Besiktas": "贝西克塔斯",
    "Buyuksehyr": "巴萨克赛尔", "Corum": "科鲁姆", "Erzurumspor": "埃尔祖鲁姆体育",
    "Eyupspor": "埃于普体育", "Fenerbahce": "费内巴切", "Galatasaray": "加拉塔萨雷",
    "Gaziantep": "加济安泰普", "Genclerbirligi": "根克勒比利吉", "Goztep": "哥兹塔比",
    "Kasimpasa": "卡斯帕萨", "Kocaelispor": "科贾埃利体育", "Konyaspor": "科尼亚体育",
    "Rizespor": "里泽体育", "Samsunspor": "萨姆松体育", "Trabzonspor": "特拉布宗体育",
    # 希腊超
    "AEK": "雅典AEK", "Aris": "阿里斯", "Asteras Tripolis": "特里波利斯之星",
    "Atromitos": "阿特罗米托斯", "Iraklis": "伊拉克利斯", "Kalamata": "卡拉马塔",
    "Kifisia": "基菲夏", "Levadeiakos": "莱瓦贾科斯", "OFI Crete": "OFI克里特",
    "Olympiakos": "奥林匹亚科斯", "PAOK": "塞萨洛尼基", "Panathinaikos": "帕纳辛奈科斯",
    "Panetolikos": "帕纳托利克斯", "Volos NFC": "沃洛斯",
    # 英冠
    "Birmingham": "伯明翰", "Blackburn": "布莱克本", "Bolton": "博尔顿",
    "Bristol City": "布里斯托尔城", "Burnley": "伯恩利", "Cardiff": "卡迪夫城",
    "Charlton": "查尔顿", "Derby": "德比郡", "Lincoln": "林肯城",
    "Middlesbrough": "米德尔斯堡", "Millwall": "米尔沃尔", "Norwich": "诺维奇",
    "Portsmouth": "朴茨茅斯", "Preston": "普雷斯顿", "QPR": "女王公园巡游者",
    "Sheffield United": "谢菲尔德联", "Southampton": "南安普顿", "Stoke": "斯托克城",
    "Swansea": "斯旺西", "Watford": "沃特福德", "West Brom": "西布罗姆维奇",
    "West Ham": "西汉姆联", "Wolves": "狼队", "Wrexham": "雷克瑟姆",
    # 西乙
    "Albacete": "阿尔瓦塞特", "Almeria": "阿尔梅里亚", "Andorra": "安道尔FC",
    "Burgos": "布尔戈斯", "Cadiz": "加的斯", "Castellon": "卡斯特利翁",
    "Celta B": "塞尔塔B队", "Ceuta": "休达", "Cordoba": "科尔多瓦",
    "Eibar": "埃瓦尔", "Eldense": "埃登斯", "Girona": "赫罗纳",
    "Granada": "格拉纳达", "Las Palmas": "拉斯帕尔马斯", "Leganes": "莱加内斯",
    "Mallorca": "马略卡", "Oviedo": "皇家奥维耶多", "Sabadell": "萨瓦德尔",
    "Sociedad B": "皇家社会B队", "Sp Gijon": "希洪竞技", "Tenerife": "特内里费",
    "Valladolid": "巴拉多利德",
    # 意乙
    "Arezzo": "阿雷佐", "Ascoli": "阿斯科利", "Avellino": "阿维利诺",
    "Benevento": "贝内文托", "Carrarese": "卡拉雷塞", "Catanzaro": "卡坦扎罗",
    "Cesena": "切塞纳", "Cremonese": "克雷莫内塞", "Empoli": "恩波利",
    "Juve Stabia": "尤韦斯塔比亚", "Mantova": "曼托瓦", "Modena": "摩德纳",
    "Padova": "帕多瓦", "Palermo": "巴勒莫", "Pisa": "比萨",
    "Sampdoria": "桑普多利亚", "Sudtirol": "南蒂罗尔", "Verona": "维罗纳",
    "Vicenza": "维琴察", "Virtus Entella": "维尔图斯恩泰拉",
    # 德乙
    "Bielefeld": "比勒费尔德", "Bochum": "波鸿", "Braunschweig": "布伦瑞克",
    "Cottbus": "科特布斯", "Darmstadt": "达姆施塔特", "Dresden": "德累斯顿迪纳摩",
    "Greuther Furth": "菲尔特", "Hannover": "汉诺威96", "Heidenheim": "海登海姆",
    "Hertha": "柏林赫塔", "Holstein Kiel": "荷尔斯泰因基尔", "Kaiserslautern": "凯泽斯劳滕",
    "Karlsruhe": "卡尔斯鲁厄", "Magdeburg": "马格德堡", "Nurnberg": "纽伦堡",
    "Osnabruck": "奥斯纳布吕克", "St Pauli": "圣保利", "Wolfsburg": "沃尔夫斯堡",
    # 法乙
    "Annecy": "昂纳西", "Boulogne": "布洛涅", "Clermont": "克莱蒙",
    "Dijon": "第戎", "Dunkerque": "敦刻尔克", "Grenoble": "格勒诺布尔",
    "Guingamp": "甘冈", "Laval": "拉瓦勒", "Metz": "梅斯",
    "Montpellier": "蒙彼利埃", "Nancy": "南锡", "Nantes": "南特",
    "Pau FC": "波城", "Red Star": "红星", "Reims": "兰斯",
    "Rodez": "罗德兹", "Sochaux": "索肖", "St Etienne": "圣埃蒂安",
    # 瑞典超
    "AIK": "AIK索尔纳", "Brommapojkarna": "布罗马博伊卡纳", "Degerfors": "代格福什",
    "Djurgarden": "尤尔加登", "Elfsborg": "埃尔夫斯堡", "GAIS": "哥德堡盖斯",
    "Goteborg": "IFK哥德堡", "Hacken": "赫根", "Halmstad": "哈尔姆斯塔德",
    "Hammarby": "哈马比", "Kalmar": "卡尔马", "Malmo FF": "马尔默",
    "Mjallby": "米亚尔比", "Orgryte": "奥尔格里特", "Sirius": "天狼星",
    "Vasteras SK": "韦斯特罗斯",
    # 瑞士超
    "Basel": "巴塞尔", "Grasshoppers": "草蜢", "Lausanne": "洛桑",
    "Lugano": "卢加诺", "Luzern": "卢塞恩", "Servette": "塞尔维特",
    "Sion": "锡永", "St. Gallen": "圣加仑", "Thun": "图恩",
    "Vaduz": "瓦杜兹", "Young Boys": "伯尔尼年轻人", "Zurich": "苏黎世",
    # 日本J1
    "Avispa Fukuoka": "福冈黄蜂", "Cerezo Osaka": "大阪樱花", "Chiba": "千叶市原",
    "FC Tokyo": "FC东京", "Gamba Osaka": "大阪钢巴", "Kashima Antlers": "鹿岛鹿角",
    "Kashiwa Reysol": "柏太阳神", "Kawasaki Frontale": "川崎前锋", "Kyoto": "京都不死鸟",
    "Machida": "町田泽维亚", "Mito": "水户蜀葵", "Nagoya Grampus": "名古屋鲸八",
    "Okayama": "冈山绿雉", "Sanfrecce Hiroshima": "广岛三箭", "Shimizu S-Pulse": "清水心跳",
    "Urawa Reds": "浦和红钻", "V-Varen Nagasaki": "长崎成功丸", "Verdy": "东京绿茵",
    "Vissel Kobe": "神户胜利船", "Yokohama F. Marinos": "横滨水手",
    # 奥超
    "A. Lustenau": "奥地利卢斯特瑙", "Altach": "阿尔塔赫", "Austria Vienna": "奥地利维也纳",
    "Grazer AK": "格拉茨AK", "Hartberg": "哈特贝格", "LASK": "林茨",
    "Ried": "里德", "SK Rapid": "维也纳快速", "Salzburg": "萨尔茨堡红牛",
    "Sturm Graz": "格拉茨风暴", "Tirol": "蒂罗尔", "Wolfsberger AC": "沃尔夫斯贝格",
    # 俄超
    "Akhmat Grozny": "格罗兹尼艾卡马特", "Akron Togliatti": "陶里亚蒂阿克隆", "Baltika": "加里宁格勒波罗的海",
    "CSKA Moscow": "莫斯科中央陆军", "Dynamo Makhachkala": "马哈奇卡拉迪纳摩", "Dynamo Moscow": "莫斯科迪纳摩",
    "FK Rostov": "罗斯托夫", "Fakel Voronezh": "沃罗涅日火炬", "Krasnodar": "克拉斯诺达尔",
    "Krylya Sovetov": "苏维埃之翼", "Lokomotiv Moscow": "莫斯科火车头", "Orenburg": "奥伦堡",
    "Rodina Moscow": "莫斯科罗迪纳", "Rubin Kazan": "喀山红宝石", "Spartak Moscow": "莫斯科斯巴达克",
    "Zenit": "圣彼得堡泽尼特",
    # 挪超
    "Aalesund": "奥勒松", "Bodo/Glimt": "博德闪耀", "Brann": "布兰",
    "Fredrikstad": "腓特烈斯塔", "HamKam": "汉坎", "KFUM Oslo": "奥斯陆KFUM",
    "Kristiansund": "克里斯蒂安松", "Lillestrom": "利勒斯特伦", "Molde": "莫尔德",
    "Rosenborg": "罗森博格", "Sandefjord": "桑德菲杰", "Sarpsborg 08": "萨普斯堡08",
    "Start": "斯达", "Tromso": "特罗姆瑟", "Valerenga": "瓦勒伦加", "Viking": "维京",
    # 巴甲
    "Athletico-PR": "巴拉纳竞技", "Atletico-MG": "米内罗竞技", "Bahia": "巴伊亚",
    "Botafogo RJ": "博塔弗戈", "Bragantino": "布拉甘蒂诺红牛", "Chapecoense-SC": "沙佩科恩斯",
    "Corinthians": "科林蒂安", "Coritiba": "科里蒂巴", "Cruzeiro": "克鲁塞罗",
    "Flamengo RJ": "弗拉门戈", "Fluminense": "弗鲁米嫩塞", "Gremio": "格雷米奥",
    "Internacional": "巴西国际", "Mirassol": "米拉索尔", "Palmeiras": "帕尔梅拉斯",
    "Remo": "雷莫", "Santos": "桑托斯", "Sao Paulo": "圣保罗",
    "Vasco": "瓦斯科达伽马", "Vitoria": "维多利亚",
    # 阿甲
    "Aldosivi": "阿尔多西维", "Argentinos Jrs": "阿根廷青年人", "Atl. Tucuman": "图库曼竞技",
    "Banfield": "班菲尔德", "Barracas Central": "巴拉卡斯中央", "Belgrano": "贝尔格拉诺",
    "Boca Juniors": "博卡青年", "Central Cordoba": "科尔多瓦中央", "Defensa y Justicia": "国防与司法",
    "Dep. Riestra": "列斯特拉体育", "Estudiantes L.P.": "拉普拉塔大学生", "Estudiantes Rio Cuarto": "里奥夸尔托大学生",
    "Gimnasia L.P.": "拉普拉塔体操", "Gimnasia Mendoza": "门多萨体操", "Huracan": "飓风",
    "Ind. Rivadavia": "里瓦达维亚独立", "Independiente": "阿根廷独立", "Instituto": "科尔多瓦学院",
    "Lanus": "拉努斯", "Newells Old Boys": "纽维尔老男孩", "Platense": "普拉滕斯",
    "Racing Club": "竞技俱乐部", "River Plate": "河床", "Rosario Central": "罗萨里奥中央",
    "San Lorenzo": "圣洛伦索", "Sarmiento Junin": "萨米恩托", "Talleres Cordoba": "塔勒雷斯",
    "Tigre": "蒂格雷", "Union de Santa Fe": "圣菲联合", "Velez Sarsfield": "萨斯菲尔德",
    # 波超
    "Cracovia": "克拉科维亚", "GKS Katowice": "卡托维兹GKS", "Gornik Zabrze": "扎布热矿工",
    "Jagiellonia": "雅盖隆", "Korona Kielce": "凯尔采王冠", "Lech Poznan": "波兹南莱赫",
    "Legia": "华沙莱吉亚", "Motor Lublin": "卢布林汽车", "Piast Gliwice": "格利维采皮亚斯特",
    "Pogon Szczecin": "什切青波贡", "Radomiak Radom": "拉多米亚克", "Rakow": "琴斯托霍瓦",
    "Slask Wroclaw": "弗罗茨瓦夫希隆斯克", "Widzew Lodz": "罗兹维泽夫", "Wieczysta Krakow": "克拉科夫维奇斯塔",
    "Wisla": "克拉科夫维斯瓦", "Wisla Plock": "普沃茨克维斯瓦", "Zaglebie": "卢宾铜矿",
    # 丹超
    "Aarhus": "奥胡斯", "Brondby": "布隆德比", "FC Copenhagen": "哥本哈根",
    "Horsens": "霍尔森斯", "Lyngby": "灵比", "Midtjylland": "中日德兰",
    "Nordsjaelland": "北西兰", "Odense": "欧登塞", "Randers FC": "兰讷斯",
    "Silkeborg": "锡尔克堡", "Sonderjyske": "桑德捷斯基", "Viborg": "维堡",
    # 芬超
    "AC Oulu": "奥卢", "Gnistan": "格尼斯坦", "HJK": "赫尔辛基HJK",
    "Ilves": "伊尔维斯", "Inter Turku": "图尔库国际", "Jaro": "雅各布斯塔德",
    "KuPS": "库普斯", "Lahti": "拉赫蒂", "Mariehamn": "玛丽港",
    "PK-35": "PK-35", "SJK": "塞伊奈约基", "TPS": "图尔库TPS", "VPS": "瓦萨VPS",
    # 罗甲
    "CFR Cluj": "克卢日", "Corvinul": "胡内多阿拉乌鸦", "Csikszereda M. Ciuc": "米耶尔库雷亚丘克",
    "Dinamo Bucuresti": "布加勒斯特迪纳摩", "FC Arges": "阿尔杰什", "FC Botosani": "博托沙尼",
    "FC Rapid Bucuresti": "布加勒斯特快速", "FC Voluntari": "沃伦塔里", "FCSB": "布加勒斯特星",
    "Farul Constanta": "康斯坦察灯塔", "Otelul": "加拉茨钢铁", "Petrolul": "普洛耶什蒂石油",
    "Sepsi Sf. Gheorghe": "圣格奥尔基塞普西", "U. Cluj": "克卢日大学", "UTA Arad": "阿拉德UTA",
    "Univ. Craiova": "克拉约瓦大学",
    # 墨超
    "Atl. San Luis": "圣路易斯竞技", "Atlante": "亚特兰特", "Atlas": "阿特拉斯",
    "Club America": "墨西哥美洲", "Club Leon": "莱昂", "Club Tijuana": "蒂华纳",
    "Cruz Azul": "蓝十字", "Guadalajara Chivas": "瓜达拉哈拉", "Juarez": "华雷斯",
    "Monterrey": "蒙特雷", "Necaxa": "内卡萨", "Pachuca": "帕丘卡",
    "Puebla": "普埃布拉", "Queretaro": "克雷塔罗", "Santos Laguna": "桑托斯拉古纳",
    "Tigres UANL": "老虎大学", "Toluca": "托卢卡", "UNAM Pumas": "美洲狮",
    # 美职联
    "Atlanta Utd": "亚特兰大联", "Austin FC": "奥斯汀", "CF Montreal": "蒙特利尔",
    "Charlotte": "夏洛特", "Chicago Fire": "芝加哥火焰", "Colorado Rapids": "科罗拉多急流",
    "Columbus Crew": "哥伦布机员", "DC United": "华盛顿联", "FC Cincinnati": "辛辛那提",
    "FC Dallas": "达拉斯", "Houston Dynamo": "休斯敦迪纳摩", "Inter Miami": "迈阿密国际",
    "Los Angeles FC": "洛杉矶FC", "Los Angeles Galaxy": "洛杉矶银河", "Minnesota United": "明尼苏达联",
    "Nashville SC": "纳什维尔", "New England Revolution": "新英格兰革命", "New York City": "纽约城",
    "New York Red Bulls": "纽约红牛", "Orlando City": "奥兰多城", "Philadelphia Union": "费城联合",
    "Portland Timbers": "波特兰伐木者", "Real Salt Lake": "皇家盐湖城", "San Diego FC": "圣迭戈",
    "San Jose Earthquakes": "圣何塞地震", "Seattle Sounders": "西雅图海湾人", "Sporting Kansas City": "堪萨斯城体育",
    "St. Louis City": "圣路易斯城", "Toronto FC": "多伦多FC", "Vancouver Whitecaps": "温哥华白浪",
}


def localize_matches(matches: list[dict]) -> list[dict]:
    """翻译展示队名并保留原始英文，不改动任何比分或比赛顺序。"""
    localized = []
    for match in matches:
        home_source, away_source = match["home"], match["away"]
        localized.append({
            **match,
            "home": TEAM_ZH.get(home_source, home_source),
            "away": TEAM_ZH.get(away_source, away_source),
            "home_source": home_source,
            "away_source": away_source,
        })
    return localized


def season_code(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    start = now.year if now.month >= 7 else now.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def csv_url(league_id: str, season: str) -> str:
    return f"https://www.football-data.co.uk/mmz4281/{season}/{league_id}.csv"


def extra_csv_url(league_id: str) -> str:
    return f"https://www.football-data.co.uk/new/{league_id}.csv"


def extra_season(config: dict, now: datetime) -> str:
    start = now.year if now.month >= 7 else now.year - 1
    if config["season_type"] == "calendar":
        return str(now.year)
    return f"{start}/{start + 1}"


def fetch_csv(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=45) as response:
        if response.status != 200:
            raise RuntimeError(f"数据源返回 HTTP {response.status}: {url}")
        return response.read().decode("utf-8-sig")


def parse_matches(csv_text: str, league_id: str) -> list[dict]:
    rows = csv.DictReader(io.StringIO(csv_text))
    required = {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
    if not rows.fieldnames or not required.issubset(rows.fieldnames):
        raise ValueError(f"{league_id} CSV 缺少必要字段: {sorted(required)}")
    matches = []
    for order, row in enumerate(rows):
        try:
            if row["Div"] != league_id or not row["FTHG"] or not row["FTAG"]:
                continue
            date = datetime.strptime(row["Date"], "%d/%m/%Y").date().isoformat()
            home, away = row["HomeTeam"].strip(), row["AwayTeam"].strip()
            if not home or not away or home == away:
                continue
            matches.append({
                "date": date,
                "time": (row.get("Time") or "").strip(),
                "home": home,
                "away": away,
                "home_goals": int(row["FTHG"]),
                "away_goals": int(row["FTAG"]),
                "order": order,
            })
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{league_id} 第 {order + 2} 行比赛数据无法解析") from exc
    matches.sort(key=lambda m: (m["date"], m["time"], m["order"]))
    if not matches:
        raise ValueError(f"{league_id} 当前赛季尚无已结束比赛")
    return matches


def parse_extra_matches(csv_text: str, league_id: str, season: str, country: str) -> list[dict]:
    """解析 new/*.csv；这类文件含多个历史赛季，且个别编号可能指向其他国家。"""
    rows = csv.DictReader(io.StringIO(csv_text))
    required = {"Country", "Season", "Date", "Home", "Away", "HG", "AG"}
    if not rows.fieldnames or not required.issubset(rows.fieldnames):
        raise ValueError(f"{league_id} CSV 缺少必要字段: {sorted(required)}")
    all_rows = list(rows)
    countries = {row["Country"].strip() for row in all_rows if row.get("Country", "").strip()}
    if countries != {country}:
        raise ValueError(f"{league_id} 文件国家不匹配：期望 {country}，实际 {sorted(countries)}")
    matches = []
    for order, row in enumerate(all_rows):
        try:
            if row["Season"] != season or not row["HG"] or not row["AG"]:
                continue
            date = datetime.strptime(row["Date"], "%d/%m/%Y").date().isoformat()
            home, away = row["Home"].strip(), row["Away"].strip()
            if not home or not away or home == away:
                continue
            matches.append({
                "date": date,
                "time": (row.get("Time") or "").strip(),
                "home": home,
                "away": away,
                "home_goals": int(row["HG"]),
                "away_goals": int(row["AG"]),
                "order": order,
            })
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{league_id} 第 {order + 2} 行比赛数据无法解析") from exc
    matches.sort(key=lambda m: (m["date"], m["time"], m["order"]))
    if not matches:
        raise ValueError(f"{league_id} {season} 赛季尚无已结束比赛")
    return matches


def standings(matches: list[dict]) -> list[dict]:
    table: dict[str, dict] = {}

    def team(name: str) -> dict:
        return table.setdefault(name, {
            "id": name, "name": name, "played": 0, "won": 0, "drawn": 0,
            "lost": 0, "gf": 0, "ga": 0, "points": 0,
        })

    for match in matches:
        home, away = team(match["home"]), team(match["away"])
        hg, ag = match["home_goals"], match["away_goals"]
        for item, scored, conceded in ((home, hg, ag), (away, ag, hg)):
            item["played"] += 1
            item["gf"] += scored
            item["ga"] += conceded
        if hg > ag:
            home["won"] += 1; home["points"] += 3; away["lost"] += 1
        elif hg < ag:
            away["won"] += 1; away["points"] += 3; home["lost"] += 1
        else:
            home["drawn"] += 1; away["drawn"] += 1
            home["points"] += 1; away["points"] += 1
    result = sorted(table.values(), key=lambda t: (-t["points"], -(t["gf"] - t["ga"]), -t["gf"], t["name"]))
    for rank, item in enumerate(result, 1):
        item["rank"] = rank
    return result


def build_dataset(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    season = season_code(now)
    leagues = []
    for config in LEAGUES:
        if config.get("format") == "extra":
            url = extra_csv_url(config["id"])
            league_season = extra_season(config, now)
            raw_matches = parse_extra_matches(fetch_csv(url), config["id"], league_season, config["country"])
        else:
            url = csv_url(config["id"], season)
            league_season = season
            raw_matches = parse_matches(fetch_csv(url), config["id"])
        matches = localize_matches(raw_matches)
        leagues.append({**config, "season": league_season, "source_url": url, "matches": matches, "teams": standings(matches)})
        untranslated = sorted({source for m in matches for source in (m["home_source"], m["away_source"]) if source not in TEAM_ZH})
        print(f"FETCH_OK {config['name']} season={league_season} matches={len(matches)} teams={len(leagues[-1]['teams'])} latest={matches[-1]['date']} untranslated={untranslated}")
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "season": season,
        "source": {
            "name": "football-data.co.uk 公开比赛结果 CSV",
            "homepage": "https://www.football-data.co.uk/",
            "note": "仅使用各赛事当前赛季已结束比赛；主文件比分为 FTHG/FTAG，扩展文件比分为 HG/AG，并校验 Season 与 Country。",
        },
        "leagues": leagues,
    }


def main() -> None:
    output = ROOT / "data" / "leagues.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    dataset = build_dataset()
    output.write_text(json.dumps(dataset, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"WROTE {output} bytes={output.stat().st_size}")


if __name__ == "__main__":
    main()
