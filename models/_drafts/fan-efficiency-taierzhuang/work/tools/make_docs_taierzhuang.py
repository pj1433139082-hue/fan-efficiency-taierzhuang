# -*- coding: utf-8 -*-
"""生成对接清单的 Excel / Word 两个版本（对外、对内）。v2：修正表头被覆盖、列宽溢出。"""
import os
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# 对外交付物统一放 data/<厂>/delivery/（模型工作区禁止 .xlsx，见 delivery/README.md）
OUT = r"E:\beikong-code\learn-model\data\fan-efficiency-taierzhuang\delivery"
XLSX_OUT = OUT
os.makedirs(OUT, exist_ok=True)
os.makedirs(XLSX_OUT, exist_ok=True)

POINTS = [
    ("1期1#鼓风机房", "4#曝气风机", "PS37217-01-BE_GFJF_0001-C_003_0001"),
    ("1期1#鼓风机房", "5#曝气风机", "PS37217-01-BE_GFJF_0001-C_003_0002"),
    ("1期1#鼓风机房", "6#曝气风机", "PS37217-01-BE_GFJF_0001-C_003_0003"),
    ("2期1#鼓风机房", "1#曝气风机", "PS37217-02-BE_GFJF_0001-C_003_0001"),
    ("2期1#鼓风机房", "2#曝气风机", "PS37217-02-BE_GFJF_0001-C_003_0002"),
    ("2期1#鼓风机房", "3#曝气风机", "PS37217-02-BE_GFJF_0001-C_003_0003"),
]
SIGNALS = [
    ("V_P", "电机功率", "kW", "分母。效率 = 气动功率 ÷ 这个数"),
    ("V_IF", "进口风量", "m³/min", "分子里的风量"),
    ("V_OP", "出口压力", "kPa", "算压比"),
    ("V_ITIT", "进口温度", "℃", "标况换算"),
]
AUX = [
    ("1期1#生化池", "1#曝气支管压力变送器", "PS37217-01-BE_SHC_0001-PIT_003_0001", "V_PIT", "核对出口压力是否合理"),
    ("1期2#生化池", "1#曝气支管压力变送器", "PS37217-01-BE_SHC_0002-PIT_003_0002", "V_PIT", "同上"),
    ("2期1#生化池", "1#曝气支管压力变送器", "PS37217-02-BE_SHC_0001-PIT_003_0002", "V_PIT", "同上"),
    ("2期2#生化池", "1#曝气支管压力变送器", "PS37217-02-BE_SHC_0002-PIT_003_0001", "V_PIT", "同上"),
    ("1期1#电量计量系统", "1#曝气风机系统多功能电表", "PS37217-01-BE_DLJLXT_0001-EM_010_0001", "V_EP", "累计电量，核对功率量级"),
    ("2期1#电量计量系统", "1#曝气风机系统多功能电表", "PS37217-02-BE_DLJLXT_0001-EM_010_0001", "V_EP", "同上"),
    ("1期1#进水提升泵房", "1#进水总流量计", "PS37217-01-BE_JSTS_0001-FIT_001_0001", "V_LF", "算气水比，判断风量是否离谱"),
    ("2期1#进水提升泵房", "1#进水总流量计", "PS37217-02-BE_JSTS_0001-FIT_001_0001", "V_LF", "同上"),
    ("2期1#生化池", "1#曝气支管气体流量计", "PS37217-02-BE_SHC_0001-FIT_005_0001", "V_GF", "当前读数「--」，采集异常"),
    ("2期2#生化池", "1#曝气支管气体流量计", "PS37217-02-BE_SHC_0002-FIT_005_0002", "V_GF", "同上"),
]
# 第一批：决定数值与有效区间，答完就能出数（顺序按影响量级）
# 已由厂家确认的 4 条：不再要求回答，列出来供对方核对（避免重复回答、也表明我们在跟）
CONFIRMED = [
    ("D-1", "V_P 功率", "电机输入功率，含变频器与电机全部损耗。", "分母口径确定。注意：由此算出的是「机组效率」，与厂家样本上的等熵/多变效率不能直接比。"),
    ("B-2", "V_OP 取压点", "表压，测点在风机内部蜗壳出口。", "出口压力口径确定，且测点是最理想位置（无沿程损失）。"),
    ("B-1", "V_OP 制式", "表压（非绝压），单位 kPa。", "压比按 (当地大气压 + 表压) / 当地大气压计算，成立。"),
    ("A-1", "V_IF 流量", "工况流量（非标况），数值由厂家面板换算得到。", "按工况计算成立；但面板值是换算值、非计量值，模型精度上限受它约束。"),
]
# 第一批（还差的）：决定数值对不对、允许跑到哪
VENDOR1 = [
    ("C-4", "★", "进口温度探头装在哪：进气管里面，还是机房环境、机壳表面？", "装错位置，部署口径静默偏约 1 点，且冬夏反向"),
    ("E-3", "★", "喘振报警对应的最小允许风量是多少？喘振保护怎么动作（降频、开旁通、停机）？", "我们用实测最低值当可调下限；真实下限更高时，一污那 67% 的换配组建议有一部分执行不了"),
    ("A-2", "★", "样本上的风量范围是多少（最小到最大）？请对照我们实测值，看是否一致、下限能否更低。", "区间划错，低负荷要么漏解、要么给出机组跑不到的风量"),
    ("A-3", "★", "面板算流量用的是哪些参数（转速、导叶开度、电流）？有没有做进口温度／压力补偿？面板上有没有「标况／工况」切换项，现在挂在哪一档？", "面板换算是模型唯一的流量来源，补偿方式决定温度变化时流量是否跟着漂"),
    ("D-5", "○", "功率是电控柜内部自算的，还是独立电表来的？电表的精度等级、多久校验一次？", "用来校验厂内电表累计电量与 V_P 是否自洽，判断 V_P 有无零点漂移"),
]
# 第二批：不影响本模型出数，影响拿效率干什么、对不对得上
VENDOR2 = [
    ("D-2", "★", "每台电机的铭牌：额定电压、电流、功率、转速、功率因数、效率；变频器型号和调速范围。", "判断读数是否合理"),
    ("B-5", "★", "进口侧有没有过滤器、消音器、滤棉？压差多少？进口压力要不要扣掉这部分？", "不扣会高估进口压力，约 1.0 个点"),
    ("C-1", "★", "厂家样本上的效率，是等熵效率、多变效率，还是含电机和变频器的机组效率？", "定义不对，整个数不能用"),
    ("E-1", "★", "每台风机铭牌：厂家、型号、出厂编号、额定风量（注明工况或标况）、额定升压、额定轴功率、电机功率、转速；六台是否同型号？", "校核效率是否超出设备能力"),

    ("D-4", "★", "V_SIT 到底是什么量？我们看它停机时还读到 60~95，判断像就地设定开度或频率，不像风量。另变频器调速范围、最低最高频率？", "下发指令前必须知道它代表什么"),
    ("E-2", "○", "有没有性能曲线或出厂试验报告，含不同风量下的效率？", "校核效率曲线"),
    ("C-2", "○", "按干空气算的，含湿空气要不要修正？", "影响不到 0.5 个点"),
    ("G-1", "○", "风机房所在地的绝对海拔（米）是多少？", "影响不到 0.1 个点，用设计值也行"),
]
DC = [
    ("J-1", "★", "2026-09-17 至 2026-10-12（约 600 小时）", "一污 6# 的 V_IF 从 530 一路漂到 159 万 m³/min，超额定一万倍；同期出口压力约 0.2 kPa、功率约 0，运行信号却是 1", "流量计故障、变送器量程设错，还是采集换算问题？这段能否重采或确认剔除？"),
    ("J-2", "★", "2025-04-30 16:00 至 2025-05-14 08:47（约 329 小时）", "二污 1#、2#、3# 三台同时读到进口温度 241~264 ℃", "三台同起同止，是采集侧还是平台侧？"),
    ("J-3", "★", "2026-02-07 14:53 至 2026-02-08 15:19（约 24 小时）", "六台同时没有任何有效数据", "是全厂停机（含风机），还是采集中断？"),
    ("J-4", "○", "2026-01-20 18:59 至 19:24", "一污 5# 有 71 个点超过额定两倍", "是不是短时校验或干扰？"),
    ("J-5", "○", "全期", "停机状态功率仍大于 1 kW：一污 5# 占 2.8%、一污 4# 占 0.3%；S_AUT 出现过 31、7、3 等非 0-1 值", "功率信号零点漂移吗？S_AUT 是否本身不可信（已不使用）？"),
]
OPS = [
    ("F-1", "★", "效率的考核线是多少？模板评分规则写「系统效率 ≥ 65% 得满分」，指的就是这个数吗？平时说的「60 到 65」是这个区间不？", "只有输出合格/不合格标记时才需要；只出效率数值可先不问"),
    ("K-1", "★", "配组模型把效率下限配成 76%，其文档定法是「实测最高效率减 8 个点」，反推按最高效率约 84% 估的，这个 84% 的来源查不到。想确认：76% 当时依据什么定的？厂家口径回来后这个下限要不要一起重定？", "卡下游配组模型上线。问配组模型作者最直接，问厂家问不出来"),
    ("K-2", "★", "每一期风机是不是接在同一根母管上，能否任意并联？另外二污常年只开 1 台，是联锁限制、规程要求，还是操作习惯？", "卡下游配组模型的可行解"),
    ("K-3", "○", "目标总风量是谁给的、多久更新一次？人工设定还是接前馈气量模型？", "下游模型输入来源"),
]
RANGES = [
    ("一污 4#", 35.0, 75.0, "71.7%", "69.5%", ""),
    ("一污 5#", 37.0, 81.0, "76.8%", "73.8%", ""),
    ("一污 6#", 71.0, 97.0, "80.9%", "75.0%", ""),
    ("二污 1#", 17.0, 31.0, "66.5%", "64.9%", ""),
    ("二污 2#", 7.0, 51.0, "69.5%", "63.0%", "7 为低负荷零星样本，不算有效下限"),
    ("二污 3#", 7.0, 51.0, "69.4%", "64.2%", "同上"),
]
CAVEAT = ("下表效率值按厂家已确认的口径计算：功率＝电机输入功率（含变频器与电机全部损耗）、出口压力＝表压（蜗壳出口）、进口风量＝工况（厂家面板换算）。"
          "注意这是「机组效率」口径，与厂家样本上的等熵／多变效率不能直接比；只作量级参考，不作验收值。")
# 冷读者第一次打开这份文件，需要先知道「谁发的、为什么找我、要我做什么、怎么回」
USAGE = [
    ("谁发的", "台儿庄曝气风机效率核算项目组发出的现场核对材料。有不清楚的直接问把这份材料转给你的人，微信／电话都行。"),
    ("我们在做什么", "给台儿庄一污、二污共 6 台曝气风机做效率核算模型：接厂里数采平台的实时数据，算出每台风机的效率（气动功率 ÷ 电机输入功率），用来判断风机跑得好不好、几台怎么搭配最省电。"),
    ("为什么找你", "公式要用 4 个量——电机功率、进风流量、出口压力、进气温度。平台上只有数值，没有单位、没有取压点、没有口径说明。这几处只要猜错一个，整列效率就平移 3~5 个点，而且从结果上看不出来（70% 和 75% 都像正常值）。这些只有设备厂家、数采维护、生产这边能确认。"),
    ("数据是哪来的", "贵厂数采平台导出的原始读数：2024-10-14 至 2026-10-17（北京时间），6 台风机 × 17 个信号，15 秒一条，共约 460 万行。第六节的「实测运行范围」就是这批数据统计出来的。数据只用于本模型，不对外提供。"),
    ("要你做什么", "按序号回 4 张表：第二节 5 条（问设备厂家；其中前 4 行是已收到的答复，核对即可、不用再答）、第三节 8 条（问设备厂家，可后补）、第四节 5 条（问数采／自控维护）、第五节 4 条（问生产／业主）。每张表最后一列「答复」留给你填。"),
    ("先回哪几条", "先回第二节剩下的 5 条：第 1 条（进口温度探头装在哪）决定效率数值，第 2、3 条（喘振最小允许风量、样本风量范围）决定机组允许跑到哪，是下游配组模型能不能上线的前提。"),
    ("怎么回、回到哪", "三种都行：①在 Word／Excel 的「答复」列直接填，把文件发回；②打印勾填后拍照发回；③按序号写文字（例：「第 2 条：电机输入功率，电控柜算的」）发回。发回给把材料转给你的人即可。"),
    ("如果不是你负责", "麻烦转给对应的人：设备参数 → 厂家或设备科；点位与读数 → 数采或自控维护；运行与考核 → 生产或项目公司。三块可以分开回，不用等齐。"),
    ("什么时候要", "希望收到材料后一周内回第一轮（第二、四节最要紧）；第三、五节可以晚一些。"),
    ("运行范围怎么用", "第六节是「实际跑到过哪里」，不是「允许跑到哪里」。允许范围以厂家喘振边界为准，不要拿这张表当设备能力依据。"),
]
# 术语、符号、编号：第一次看的人不用猜
TERMS = [
    ("V_P", "电机功率的信号名", "kW", "正文简称「功率」，是效率的分母"),
    ("V_IF", "进风流量的信号名", "m³/min", "平台读数，未标工况还是标况，本文按工况算"),
    ("V_OP", "出口压力的信号名", "kPa", "正文简称「背压」，本文按表压处理"),
    ("V_ITIT", "进气温度的信号名", "℃", "只用于标况换算"),
    ("m³/min 与 Nm³/min", "工况流量 与 标况流量", "—", "工况＝现场温度压力下的体积流量；标况＝换算到 20℃、1 个大气压后的流量。两者差 2%~5%，本文全部按工况"),
    ("★ 与 ○", "必答 与 可最后补", "—", "★ 影响数值或能否上线；○ 影响对账与完善"),
    ("效率", "气动功率 ÷ 电机输入功率", "0~1（文中写成 %）", "本文算的是多变压缩效率，不是厂家样本上的等熵效率"),
    ("编号字母", "D 功率 / B 测点与压力 / A 流量口径 / C 温度湿度 / E 设备能力 / G 海拔 / J 数据异常 / F 考核 / K 运行与配组", "—", "同一字母属于同一类问题，可以一起答"),
]
NAVY = "FF1F3A5F"
GREY = "FFF5F6F7"
THIN = Side(style="thin", color="FFC9D2DC")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HDR_FONT = Font(name="微软雅黑", size=10, bold=True, color="FFFFFFFF")
BODY_FONT = Font(name="微软雅黑", size=9.5)
TITLE_FONT = Font(name="微软雅黑", size=14, bold=True, color=NAVY)
SUB_FONT = Font(name="微软雅黑", size=9, color="FF5A6580")
NOTE_FONT = Font(name="微软雅黑", size=9, italic=True, color="FF8A5A2B")
BOLD_NAVY = Font(name="微软雅黑", size=9.5, bold=True, color=NAVY)



def _seq(rows):
    """对外版：把内部字母编号列换成 1、2、3… 连续序号（收件人看的是序号，不是我们的编号）。"""
    return [tuple([str(i + 1)] + list(r[1:])) for i, r in enumerate(rows)]


def _renum_terms(terms):
    """对外版把「编号字母」那行换成「序号」说明；内部版保留字母含义。"""
    out = []
    for r in terms:
        if r[0] == "编号字母":
            out.append(("序号", "每张表内 1、2、3… 连续编号", "—",
                        "按序号回即可，例「第 2 条：电机输入功率，电控柜算的」"))
        else:
            out.append(r)
    return out


def add_title(ws, text, sub, span=5):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    c = ws.cell(row=1, column=1, value=text)
    c.font = TITLE_FONT
    c.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 30
    if sub:
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=span)
        c2 = ws.cell(row=2, column=1, value=sub)
        c2.font = SUB_FONT
        c2.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[2].height = 34


def put_header(ws, headers):
    """在下一可用行放表头，返回表头行号。"""
    r = ws.max_row + 1
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=r, column=i, value=h)
        c.font = HDR_FONT
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
    ws.row_dimensions[r].height = 26
    return r


def put_rows(ws, rows, hdr_row, zebra=True):
    r = hdr_row
    for k, row in enumerate(rows):
        r += 1
        for i, v in enumerate(row, start=1):
            c = ws.cell(row=r, column=i, value=v)
            c.font = BODY_FONT
            c.border = BORDER
            c.alignment = Alignment(vertical="top", wrap_text=True)
            if zebra and k % 2 == 1:
                c.fill = PatternFill("solid", fgColor=GREY)
    return r


def set_widths(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def finish(wb):
    """说明与术语表用 A4 竖版；清单类工作表用 A4 横版 + 按页宽缩放（否则 6 列会折页）。"""
    from openpyxl.worksheet.properties import PageSetupProperties
    narrow = ("说明", "术语")
    for ws in wb.worksheets:
        ws.sheet_view.showGridLines = False
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        if ws.title.startswith(narrow):
            ws.page_setup.orientation = "portrait"
            continue
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        ws.print_title_rows = "1:3"


def build_xlsx(path, internal):
    CODE = "编号" if internal else "序号"
    _g = globals()
    if internal:
        VENDOR1, VENDOR2, DC, OPS, TERMS = (_g["VENDOR1"], _g["VENDOR2"], _g["DC"], _g["OPS"], _g["TERMS"])
        CONFIRMED = _g["CONFIRMED"]
    else:
        VENDOR1 = _seq(_g["VENDOR1"])
        VENDOR2 = _seq(_g["VENDOR2"])
        DC = _seq(_g["DC"])
        OPS = _seq(_g["OPS"])
        TERMS = _renum_terms(_g["TERMS"])
        CONFIRMED = _g["CONFIRMED"]
    wb = Workbook()
    wb.remove(wb.active)

    # 说明
    ws = wb.create_sheet("说明（先看这里）")
    add_title(ws, "台儿庄曝气风机效率核算 · 现场对接清单",
              "2026-10-17　｜　" + ("内部版（含影响分析）" if internal else "对外版（直接发人）"), span=2)
    r = 4
    for k, v in USAGE:
        a = ws.cell(row=r, column=1, value=k)
        a.font = BOLD_NAVY
        a.alignment = Alignment(vertical="top", wrap_text=True)
        b = ws.cell(row=r, column=2, value=v)
        b.font = BODY_FONT
        b.alignment = Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[r].height = 16 * (1 + len(v) // 46)
        r += 1
    set_widths(ws, [16, 104])

    # 术语与编号
    ws = wb.create_sheet("术语与编号")
    add_title(ws, "术语、符号与编号（第一次看请先看这张表）",
              "正文里出现的信号名、单位、符号都在这里；看不懂哪个词，先查这张表再看后面。", span=4)
    h = put_header(ws, ["名称／符号", "是什么", "单位", "说明"])
    put_rows(ws, [list(x) for x in TERMS], h)
    set_widths(ws, [22, 44, 16, 60])

    # 点位对照
    ws = wb.create_sheet("一、点位对照")
    add_title(ws, "一、点位对照",
              "这一节不用回答，是给你核对的：确认下面的点位编号对应的就是这台风机。每台机的 17 个信号共用同一个点位编号，靠信号名称区分——别只按编号建索引，那样 17 个信号会互相覆盖。", span=5)
    h = put_header(ws, ["设施名称", "设备名称", "数采点位编号", "数采信号名称", "说明"])
    put_rows(ws, [[a, b, c, "（每台 17 个信号）", "曝气风机"] for a, b, c in POINTS], h)
    ws.cell(row=ws.max_row + 2, column=1, value="—— 只取这 4 个信号 ——").font = BOLD_NAVY
    h = put_header(ws, ["数采信号名称", "含义", "单位", "在公式里做什么", ""])
    put_rows(ws, [[s, n, u, w, ""] for s, n, u, w in SIGNALS], h)
    ws.cell(row=ws.max_row + 2, column=1, value="—— 辅助点位（交叉核对，不进公式）——").font = BOLD_NAVY
    h = put_header(ws, ["设施名称", "设备名称", "数采点位编号", "数采信号名称", "用途"])
    put_rows(ws, [list(x) for x in AUX], h)
    set_widths(ws, [22, 30, 46, 20, 30])

    # 第一批问题：已确认 4 条 + 还剩 5 条
    IMPACT_HDR = "影响（内部分析）" if internal else "为什么问（答错会怎样）"
    ws = wb.create_sheet("二、问厂家-已确认+还剩5条")
    add_title(ws, "二、问设备厂家：已确认 4 条，还剩 5 条",
              "前 4 行是已经收到的答复，列在这里供你核对，不用再回答（答复列标「已确认」）。后面 5 行请填最后那列「答复」：第 1 条（探头位置）决定效率数值，后 4 条决定风机允许跑到哪。", span=5)
    h = put_header(ws, [CODE, "必答", "请回答的问题 ／ 已确认的答复", IMPACT_HDR, "答复（请在此填写）"])
    put_rows(ws, [[("—" if not internal else c), "✔", sig + "：" + rep, im, "已确认"] for c, sig, rep, im in CONFIRMED]
             + [[c, s, q, im, ""] for c, s, q, im in VENDOR1], h)
    set_widths(ws, [8, 6, 62, 38, 26])

    # 第二批问题
    ws = wb.create_sheet("三、问厂家-可后补8条")
    add_title(ws, "三、问设备厂家：这 8 条可后补（做对账、给下游用）",
              "不影响本模型出数，影响的是「拿效率干什么」和「对不对得上」。★ 的优先，○ 的可以最后补。填「答复」列。", span=5)
    h = put_header(ws, [CODE, "必答", "请回答的问题", IMPACT_HDR, "答复（请在此填写）"])
    put_rows(ws, [[c, s, q, im, ""] for c, s, q, im in VENDOR2], h)
    set_widths(ws, [8, 6, 62, 38, 26])

    # 数采问题
    ws = wb.create_sheet("四、问数采维护")
    add_title(ws, "四、问数采或自控维护：5 条（独立一条线）",
              "这 5 条只看数据本身，不用另外导数据。我们从 2024-10 至 2026-10 的读数里查出三段成块异常，模型已整段剔除，只需确认性质。填最后那列「答复」。", span=6)
    h = put_header(ws, [CODE, "必答", "时段（北京时间）", "看到的现象", "要确认的", "答复（请在此填写）"])
    put_rows(ws, [list(x) + [""] for x in DC], h)
    set_widths(ws, [8, 6, 30, 56, 40, 26])

    # 生产问题
    ws = wb.create_sheet("五、问生产或业主")
    add_title(ws, "五、问生产或业主：4 条（优先级最低）",
              "这 4 条不改变效率数值本身，只影响效率拿来怎么用；优先级低于前两张表，可以最后回。填「答复」列。", span=5)
    hdr = [CODE, "必答", "请回答的问题"] + (["卡的是哪一步"] if internal else []) + ["答复（请在此填写）"]
    h = put_header(ws, hdr)
    rows = [[c, s, q] + ([n] if internal else []) + [""] for c, s, q, n in OPS]
    put_rows(ws, rows, h)
    set_widths(ws, [8, 6, 64] + ([34] if internal else []) + [26])

    # 运行范围
    ws = wb.create_sheet("六、实测运行范围")
    add_title(ws, "六、实测运行范围（给厂家对照用，不需要回答）", CAVEAT, span=6)
    h = put_header(ws, ["机组", "风量下限 m³/min", "风量上限 m³/min", "最高效率", "效率中位", "备注"])
    put_rows(ws, [list(x) for x in RANGES], h)
    note = ws.cell(row=ws.max_row + 2, column=1,
                   value="说明：这是「实际跑到过哪里」，不是「允许跑到哪里」。允许范围以厂家喘振边界为准。")
    note.font = NOTE_FONT
    ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=6)
    set_widths(ws, [14, 16, 16, 13, 13, 34])

    finish(wb)
    wb.save(path)
    return path


# ---------------- Word ----------------
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

USABLE_CM = 17.2


def set_cn_font(run, name="宋体", size=10.5, bold=False, color=None):
    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    if color:
        run.font.color.rgb = RGBColor(*color)
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), name)


def doc_heading(doc, text, size=14, new_page=False):
    p = doc.add_paragraph()
    if new_page:
        p.paragraph_format.page_break_before = True
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after = Pt(6)
    set_cn_font(p.add_run(text), "黑体", size, True, (0x1F, 0x3A, 0x5F))
    return p


def doc_para(doc, text, size=10, italic=False, color=None, bold=False):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.3
    r = p.add_run(text)
    set_cn_font(r, "宋体", size, bold, color)
    r.italic = italic
    return p


def shade(cell, hexcolor):
    tcPr = cell._tc.get_or_add_tcPr()
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:fill"), hexcolor)
    tcPr.append(el)


TBLPR_ORDER = ["tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize",
               "tblStyleColBandSize", "tblW", "jc", "tblCellSpacing", "tblInd", "tblBorders",
               "shd", "tblLayout", "tblCellMar", "tblLook", "tblCaption", "tblDescription"]


def _sort_tbl_pr(tblPr):
    """把 w:tblPr 的子元素按 schema 顺序重排；否则 Word/OpenXML 校验报 unexpected child。"""
    idx = {qn("w:" + n): i for i, n in enumerate(TBLPR_ORDER)}
    kids = list(tblPr)
    for k in kids:
        tblPr.remove(k)
    for k in sorted(kids, key=lambda e: idx.get(e.tag, 99)):
        tblPr.append(k)


def doc_table(doc, headers, rows, widths):
    assert abs(sum(widths) - USABLE_CM) < 0.35, (headers, sum(widths))
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for i, h in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = ""
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_cn_font(p.add_run(h), "黑体", 9, True, (0xFF, 0xFF, 0xFF))
        shade(cell, "1F3A5F")
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            p.paragraph_format.space_after = Pt(1)
            set_cn_font(p.add_run(str(v)), "宋体", 8.5)
    # 固定列宽：tblLayout=fixed + tblW + gridCol + 每格 tcW，缺一 LibreOffice 会均分
    tbl = t._tbl
    tblPr = tbl.tblPr
    for tag in ("w:tblLayout", "w:tblW"):
        for el in tblPr.findall(qn(tag)):
            tblPr.remove(el)
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    tblW = OxmlElement("w:tblW")
    tblW.set(qn("w:type"), "dxa")
    tblW.set(qn("w:w"), str(int(sum(widths) * 567)))
    tblPr.append(tblW)
    _sort_tbl_pr(tblPr)
    for i, w in enumerate(widths):
        t.columns[i].width = Cm(w)
    for r in t.rows:
        for i, w in enumerate(widths):
            r.cells[i].width = Cm(w)
    # 行不跨页 + 表头行在续页重复（避免一行被切成两页）
    for ri, row in enumerate(t.rows):
        trPr = row._tr.get_or_add_trPr()
        trPr.append(OxmlElement("w:cantSplit"))
        if ri == 0:
            trPr.append(OxmlElement("w:tblHeader"))
    return t


def build_docx(path, internal):
    CODE = "编号" if internal else "序号"
    _g = globals()
    if internal:
        VENDOR1, VENDOR2, DC, OPS, TERMS = (_g["VENDOR1"], _g["VENDOR2"], _g["DC"], _g["OPS"], _g["TERMS"])
        CONFIRMED = _g["CONFIRMED"]
    else:
        VENDOR1 = _seq(_g["VENDOR1"])
        VENDOR2 = _seq(_g["VENDOR2"])
        DC = _seq(_g["DC"])
        OPS = _seq(_g["OPS"])
        TERMS = _renum_terms(_g["TERMS"])
        CONFIRMED = _g["CONFIRMED"]
    doc = Document()
    IMP_COL = "影响（内部分析）" if internal else "为什么问（答错会怎样）"
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(1.9)
    sec.top_margin = sec.bottom_margin = Cm(2.0)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn_font(p.add_run("台儿庄曝气风机效率核算 · 现场对接清单"), "黑体", 17, True, (0x1F, 0x3A, 0x5F))
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn_font(p2.add_run(("内部版（含影响分析）" if internal else "对外版　直接发人") + "　｜　2026-10-17"),
                "宋体", 9, False, (0x5A, 0x65, 0x80))

    doc_heading(doc, "说明（第一次看请从这一段读起）")
    for k, v in USAGE:
        doc_para(doc, f"· {k}：{v}")

    doc_heading(doc, "术语、符号与编号")
    doc_para(doc, "正文里出现的信号名、单位、符号都在下面这张表里；看不懂哪个词，先查这里再往下看。", 9, italic=True, color=(0x5A, 0x65, 0x80))
    doc_table(doc, ["名称／符号", "是什么", "单位", "说明"],
              [[a, b, c, d] for a, b, c, d in TERMS], [2.9, 4.4, 1.7, 8.2])

    doc_heading(doc, "一、点位对照（不用回答，核对即可）")
    doc_para(doc, "请确认：下面的点位编号对应的就是这台风机。每台机的 17 个信号共用同一个点位编号，靠信号名称区分。别只按编号建索引，那样 17 个信号会互相覆盖。")
    doc_table(doc, ["设施名称", "设备名称", "数采点位编号"],
              [[a, b, c] for a, b, c in POINTS], [4.0, 3.8, 9.4])
    doc_para(doc, "")
    doc_para(doc, "只取这 4 个信号：")
    doc_table(doc, ["数采信号名称", "含义", "单位", "在公式里做什么"],
              [[s, n, u, w] for s, n, u, w in SIGNALS], [3.2, 3.0, 2.0, 9.0])
    doc_para(doc, "")
    doc_para(doc, "辅助点位（交叉核对用，不进公式）：")
    doc_table(doc, ["设施名称", "设备名称", "数采点位编号", "信号", "用途"],
              [[a, b, c, d, e] for a, b, c, d, e in AUX], [2.8, 3.6, 7.4, 1.4, 2.0])

    doc_heading(doc, "二、问设备厂家：已确认 4 条，还剩 5 条", new_page=True)
    doc_para(doc, "前 4 行是已经收到的答复，列在这里供你核对，不用再回答（答复列标「已确认」）。后面 5 行请填最后那列「答复」，一行一条：第 1 条（温度探头位置）决定效率数值，后 4 条决定风机允许跑到哪。")
    doc_table(doc, [CODE, "必答", "请回答的问题 ／ 已确认的答复", IMP_COL, "答复（请填）"],
              [[("—" if not internal else c), "✔", sig + "：" + rep, im, "已确认"] for c, sig, rep, im in CONFIRMED]
              + [[c, s, q, im, ""] for c, s, q, im in VENDOR1], [1.2, 1.1, 6.7, 4.8, 3.4])
    doc_para(doc, "还剩的 5 条里，「喘振最小风量」和「样本风量范围」是下游配组模型能不能上线的前提：拿实测最低值当可调下限，厂家真实下限更高的话，换配组建议有一部分执行不了。", 9, italic=True, color=(0x8A, 0x5A, 0x2B))

    doc_heading(doc, "三、问设备厂家：这 8 条可后补（做对账、给下游用）", new_page=True)
    doc_para(doc, "不影响本模型出数，影响的是「拿效率干什么」和「对不对得上」。★ 的优先，○ 的可以最后补。同样填「答复」列。其中关于效率定义和铭牌参数那两条（★）建议跟第二节一起问：我们算出的是机组效率，跟样本上的效率不先对齐定义，两边数字永远对不上。")
    doc_table(doc, [CODE, "必答", "请回答的问题", IMP_COL, "答复（请填）"],
              [[c, s, q, im, ""] for c, s, q, im in VENDOR2], [1.2, 1.1, 6.7, 4.8, 3.4])

    doc_heading(doc, "四、问数采或自控维护：5 条（独立一条线）", new_page=True)
    doc_para(doc, "这 5 条只看数据本身，不用另外导数据。我们从 2024-10 至 2026-10 的读数里查出三段成块异常，模型已整段剔除，只需确认性质。填最后那列「答复」。")
    doc_table(doc, [CODE, "必答", "时段（北京时间）", "看到的现象", "要确认的", "答复（请填）"],
              [[c, s, tm, ph, ask, ""] for c, s, tm, ph, ask in DC], [1.1, 1.1, 3.4, 4.6, 4.2, 2.8])

    doc_heading(doc, "五、问生产或业主：4 条（优先级最低）", new_page=True)
    doc_para(doc, "这 4 条不改变效率数值本身，只影响效率拿来怎么用；可以最后回。填「答复」列。")
    doc_table(doc, [CODE, "必答", "请回答的问题", IMP_COL, "答复（请填）"],
              [[c, s, q, n, ""] for c, s, q, n in OPS], [1.3, 1.1, 7.8, 3.9, 3.1])

    doc_heading(doc, "六、实测运行范围（给厂家对照用，不需要回答）", new_page=True)
    doc_para(doc, CAVEAT, 9, italic=True, color=(0x8A, 0x5A, 0x2B))
    doc_table(doc, ["机组", "风量下限 m³/min", "风量上限 m³/min", "最高效率", "效率中位", "备注"],
              [[n, lo, hi, pk, md, note] for n, lo, hi, pk, md, note in RANGES],
              [2.9, 3.0, 3.0, 2.6, 2.6, 3.1])
    doc_para(doc, "说明：这是「实际跑到过哪里」，不是「允许跑到哪里」。允许范围以厂家喘振边界为准。", 9)

    doc_heading(doc, "七、怎么回、回到哪", new_page=True)
    for line in [
        "填表：在 Word 或 Excel 的「答复」列直接写，回传文件即可。",
        "打印：勾填后拍照发回。",
        "文字回：按序号写（例：「第 2 条：电机输入功率，电控柜算的」），直接发回给把材料转给你的人。",
        "三块人可分开回、不用等齐：设备参数 → 厂家／设备科；点位与读数 → 数采／自控维护；运行与考核 → 生产／项目公司。",
        "如果这份不是发给你的，麻烦转给对应的人。",
        "铭牌和样本请拍清楚型号、出厂编号和额定参数那几行；有竣工图或设备表的，麻烦给设备表那一页（里面标了安装位置）。",
        "希望收到材料后一周内回第二、第四节；第三、第五节可以晚一些。",
    ]:
        doc_para(doc, "· " + line)

    doc.save(path)
    return path


# ---------------- Markdown（可复制文案版，与 office 同源） ----------------
MD_TITLE = "台儿庄曝气风机效率核算 · 现场对接清单"
MD_DATE = "2026-10-18"


def _md_cell(x):
    return str(x).replace("|", "／").replace("\n", " ")


def _md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_md_cell(c) for c in r) + " |")
    return "\n".join(out)


def build_md(path, internal=False):
    CODE = "编号" if internal else "序号"
    _g = globals()
    if internal:
        VENDOR1, VENDOR2, DC, OPS, TERMS = (_g["VENDOR1"], _g["VENDOR2"], _g["DC"], _g["OPS"], _g["TERMS"])
        CONFIRMED = _g["CONFIRMED"]
    else:
        VENDOR1 = _seq(_g["VENDOR1"])
        VENDOR2 = _seq(_g["VENDOR2"])
        DC = _seq(_g["DC"])
        OPS = _seq(_g["OPS"])
        TERMS = _renum_terms(_g["TERMS"])
        CONFIRMED = _g["CONFIRMED"]
    L = []
    a = L.append
    imp = "影响（内部分析）" if internal else "为什么问（答错会怎样）"

    a("# " + MD_TITLE)
    a("")
    a(("内部版（含影响分析）" if internal else "对外版 · 直接发人")
      + " ｜ " + MD_DATE
      + " ｜ 本文件与同目录 xlsx / docx 内容同源，三份任选一种填回即可。")
    a("")

    a("## 说明（第一次看请从这一段读起）")
    a("")
    for k, v in USAGE:
        a("- **" + k + "**：" + v)
    a("")

    a("## 术语、符号与编号")
    a("")
    a("正文里出现的信号名、单位、符号都在下面这张表里；看不懂哪个词，先查这里再往下看。")
    a("")
    a(_md_table(["名称／符号", "是什么", "单位", "说明"], TERMS))
    a("")

    a("## 一、点位对照（不用回答，核对即可）")
    a("")
    a("请确认：下面的点位编号对应的就是这台风机。每台机的 17 个信号共用同一个点位编号，靠信号名称区分。")
    a("别只按编号建索引，那样 17 个信号会互相覆盖。")
    a("")
    a("### 1.1 六台风机")
    a("")
    a(_md_table(["设施名称", "设备名称", "数采点位编号"], POINTS))
    a("")
    a("### 1.2 我们只取这 4 个信号")
    a("")
    a(_md_table(["数采信号名称", "含义", "单位", "在公式里做什么"], SIGNALS))
    a("")
    a("### 1.3 辅助点位（交叉核对用，不进公式）")
    a("")
    a(_md_table(["设施名称", "设备名称", "数采点位编号", "信号", "用途"], AUX))
    a("")

    a("## 二、问设备厂家：已确认 4 条，还剩 5 条")
    a("")
    a("前 4 行是已经收到的答复，列在这里供你核对，**不用再回答**（答复列标「已确认」）。后面 5 行请填最后那列「答复」，"
      "一行一条：第 1 条（温度探头位置）决定效率数值，后 4 条决定风机允许跑到哪。")
    a("")
    a(_md_table([CODE, "必答", "请回答的问题 ／ 已确认的答复", imp, "答复（请填）"],
                [[("—" if not internal else c), "✔", sig + "：" + rep, im, "已确认"] for c, sig, rep, im in CONFIRMED]
                + [[c, s, q, im, ""] for c, s, q, im in VENDOR1]))
    a("")
    a("> 还剩的 5 条里，**喘振最小风量和样本风量范围是下游配组模型能不能上线的前提**："
      "我们现在只能拿实测最低值当可调下限，厂家真实下限更高的话，换配组建议有一部分执行不了。历史区间不等于厂家允许区间。")
    a("")

    a("## 三、问设备厂家：这 8 条可后补（做对账、给下游用）")
    a("")
    a("不影响本模型出数，影响的是「拿效率干什么」和「对不对得上」。★ 的优先，○ 的可以最后补。同样填「答复」列。"
      "其中关于**效率定义**和**铭牌参数**那两条（★）建议跟第二节一起问：我们算出的是机组效率，跟样本上的效率不先对齐定义，两边数字永远对不上。")
    a("")
    a(_md_table([CODE, "必答", "请回答的问题", imp, "答复（请填）"],
                [[c, s, q, im, ""] for c, s, q, im in VENDOR2]))
    a("")

    a("## 四、问数采或自控维护：5 条（独立一条线）")
    a("")
    a("这 5 条只看数据本身，不用另外导数据。我们从 2024-10 至 2026-10 的读数里查出三段成块异常，模型已整段剔除，只需确认性质。")
    a("填最后那列「答复」。")
    a("")
    a(_md_table([CODE, "必答", "时段（北京时间）", "看到的现象", "要确认的", "答复（请填）"],
                [[c, s, tm, ph, ask, ""] for c, s, tm, ph, ask in DC]))
    a("")

    a("## 五、问生产或业主：4 条（优先级最低）")
    a("")
    a("这 4 条不改变效率数值本身，只影响效率拿来怎么用；可以最后回。填「答复」列。")
    a("")
    a(_md_table([CODE, "必答", "请回答的问题", imp, "答复（请填）"],
                [[c, s, q, im, ""] for c, s, q, im in OPS]))
    a("")

    a("## 六、实测运行范围（给厂家对照用，不需要回答）")
    a("")
    a("> " + CAVEAT)
    a("")
    a(_md_table(["机组", "风量下限 m³/min", "风量上限 m³/min", "最高效率", "效率中位", "备注"],
                [[n, lo, hi, pk, md, note] for n, lo, hi, pk, md, note in RANGES]))
    a("")
    a("说明：这是「实际跑到过哪里」，不是「允许跑到哪里」。允许范围以厂家喘振边界为准。")
    a("")

    a("## 七、怎么回、回到哪")
    a("")
    for line in [
        "填表：在 Word 或 Excel 的「答复」列直接写，回传文件即可。",
        "打印：勾填后拍照发回。",
        "文字回：按序号写（例：「第 2 条：电机输入功率，电控柜算的」），直接发回给把材料转给你的人。",
        "三块人可分开回、不用等齐：设备参数 → 厂家／设备科；点位与读数 → 数采／自控维护；运行与考核 → 生产／项目公司。",
        "如果这份不是发给你的，麻烦转给对应的人。",
        "铭牌和样本请拍清楚型号、出厂编号和额定参数那几行；有竣工图或设备表的，麻烦给设备表那一页（里面标了安装位置）。",
        "希望收到材料后一周内回第二、第四节；第三、第五节可以晚一些。",
    ]:
        a("- " + line)
    a("")

    a("## 八、可直接复制的发送文案")
    a("")
    a("下面每一段都是**完整独立的一份**，直接整段复制发出去；不要只挑几条发——缺一条，对方就不会回那一条。")
    a("")

    a("### 8.1 微信／钉钉 · 设备厂家（还剩 5 条 + 8 条可后补）")
    a("")
    a("```")
    a("您好，台儿庄曝气风机效率核算这边。上次那 4 条（功率口径、取压点、表压还是绝压、流量是工况还是标况）已经收到了，谢谢。")
    a("还剩 5 条想再确认一下，另外 8 条不急、有时间再回。")
    a("")
    a("一、还剩的 5 条（第 1 条最要紧，决定效率数值；第 2、3 条决定机组允许跑到哪）：")
    for i, (c, s, q, im) in enumerate(VENDOR1, 1):
        a(str(i) + ". " + q)
    a("")
    a("二、可后补的 8 条：")
    for i, (c, s, q, im) in enumerate(VENDOR2, 1):
        a(str(i) + ". " + q)
    a("")
    a("以上按序号回就行，例：第 2 条 喘振报警最小允许风量 30 m3/min，保护动作是降频。完整清单见附件。")
    a("```")
    a("")

    a("### 8.2 微信／钉钉 · 数采或自控维护（5 条全在这）")
    a("")
    a("```")
    a("在吗，麻烦看几个数采的事。")
    a("")
    a("这次用的是 2024-10 到 2026-10 的数据，做台儿庄 6 台曝气风机的效率核算。检查时发现三段成块的问题，模型已经把这几段整段剔除，不影响其他时段，但想确认这几段到底是什么、以后还会不会出现。")
    a("")
    for i, (c, s, tm, ph, ask) in enumerate(DC, 1):
        a(str(i) + ". " + tm + "：" + ph + "。要确认的是：" + ask)
    a("")
    a("这几条不用另外导数据，能说是哪类问题就行。")
    a("```")
    a("")

    a("### 8.3 微信／钉钉 · 生产或业主（4 条全在这）")
    a("")
    a("```")
    a("有几个跟「用不用效率这个数」相关的问题，比前面那些轻，你方便的时候回就行。")
    a("")
    for i, (c, s, q, im) in enumerate(OPS, 1):
        a(str(i) + ". " + q)
    a("")
    a("其中第五节第 2 条（76% 下限的依据）严格说该问当初写配组模型的人，不是生产口；问不到作者再走生产／项目组。")
    a("```")
    a("")

    a("### 8.4 邮件版（发给设备厂家，正式一点）")
    a("")
    a("```")
    a("主题：台儿庄污水处理厂曝气风机效率核算 — 参数确认（附清单）")
    a("")
    a("您好，")
    a("")
    a("这次要对台儿庄一污、二污共 6 台离心曝气风机做实时效率核算，用现场的功率、进口风量、出口压力和进口温度算风机效率。模型已经建成，现在需要确认几个设备侧的参数口径。")
    a("")
    a("涉及机组：")
    a("- 1期1#鼓风机房：4#、5#、6# 曝气风机")
    a("- 2期1#鼓风机房：1#、2#、3# 曝气风机")
    a("")
    a("需要确认的一共 5 条（上次的 4 条已收到），按对计算结果的影响排了序：第 1 条决定数值是否可信，第 2、3 条决定机组允许区间，请优先回复；另外 8 条可以后补。")
    a("")
    a("第一批（还剩 5 条，请优先）")
    for i, (c, s, q, im) in enumerate(VENDOR1, 1):
        a(str(i) + ". " + q)
    a("")
    a("第二批（可后补 8 条）")
    for i, (c, s, q, im) in enumerate(VENDOR2, 1):
        a(str(i) + ". " + q)
    a("")
    a("实测运行范围如下（2025-05 至 2026-10 的正常运行时段）。效率按贵方已确认的口径计算（功率＝电机输入功率含全部损耗、压力＝表压·蜗壳出口、风量＝工况且由面板换算），仅供参考量级，不作验收值：")
    a("")
    for n, lo, hi, pk, md, _ in RANGES:
        a("- " + n + "：" + str(lo) + " ~ " + str(hi) + " m³/min，最高 " + pk + "，中位 " + md)
    a("")
    a("以上资料如蒙提供，这边就能把参数校核完。谢谢。")
    a("")
    a("有不清楚的，直接问把这份材料转给你的人（微信／电话）。")
    a("```")
    a("")

    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    outs = [
        build_xlsx(os.path.join(XLSX_OUT, "对接清单-对外.xlsx"), False),
        build_xlsx(os.path.join(XLSX_OUT, "对接清单-内部.xlsx"), True),
        build_docx(os.path.join(OUT, "对接清单-对外.docx"), False),
        build_docx(os.path.join(OUT, "对接清单-内部.docx"), True),
        build_md(os.path.join(OUT, "对外发送-台儿庄点位与问题清单.md"), False),
    ]
    for p in outs:
        print(f"{os.path.getsize(p):>8} B  {p}")
