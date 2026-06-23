import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from scipy.spatial import KDTree
from supabase import create_client
import io

# ---------- 连接 Supabase ----------
SUPABASE_URL = "https://cfodnwbbndbdkhgjcbwx.supabase.co"
SUPABASE_KEY = "sb_publishable_dqPMk33iGwwSmxhMWLDsvw_rzD-k6E9"
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------- 工具函数 ----------
def match_points(theo_xyz, meas_xyz, max_dist=500):
    """用KDTree对每个理论点匹配最近实测点"""
    if len(meas_xyz) == 0:
        return [], [], []
    tree = KDTree(meas_xyz)
    dist, idx = tree.query(theo_xyz)
    match_mask = dist <= max_dist
    return match_mask, idx, dist

def parse_txt(file):
    """解析点号,X,Y,Z的txt文件"""
    df = pd.read_csv(file, header=None, names=["point_name","x","y","z"])
    return df

def get_box_edges(points):
    """根据点集生成包围盒的12条线段"""
    if len(points) == 0:
        return [], [], []
    x = points[:,0]; y = points[:,1]; z = points[:,2]
    xmin, xmax = x.min(), x.max()
    ymin, ymax = y.min(), y.max()
    zmin, zmax = z.min(), z.max()
    # 8个角点
    corners = np.array([
        [xmin,ymin,zmin], [xmax,ymin,zmin], [xmax,ymax,zmin], [xmin,ymax,zmin],
        [xmin,ymin,zmax], [xmax,ymin,zmax], [xmax,ymax,zmax], [xmin,ymax,zmax]
    ])
    # 12条边
    edges = [
        (0,1),(1,2),(2,3),(3,0),  # 底面
        (4,5),(5,6),(6,7),(7,4),  # 顶面
        (0,4),(1,5),(2,6),(3,7)   # 垂直棱
    ]
    xe, ye, ze = [], [], []
    for e in edges:
        xe.extend([corners[e[0]][0], corners[e[1]][0], None])
        ye.extend([corners[e[0]][1], corners[e[1]][1], None])
        ze.extend([corners[e[0]][2], corners[e[1]][2], None])
    return xe, ye, ze

# ---------- 页面配置 ----------
st.set_page_config(layout="wide")
st.title("🚢 船体总组尺寸控制平台")

# 初始化 session_state 用于跨页面传递数据
if "current_positions" not in st.session_state:
    st.session_state.current_positions = None
if "current_matched_theo" not in st.session_state:
    st.session_state.current_matched_theo = None

# ---------- 侧边栏：总段和分段选择 ----------
st.sidebar.header("📋 总段 & 分段")
assemblies = supabase.table("assemblies").select("*").execute().data
assembly_names = [a["name"] for a in assemblies]
if not assembly_names:
    st.sidebar.warning("暂无总段，请先上传数据")
    st.stop()

selected_assembly = st.sidebar.selectbox("选择总段", assembly_names)
assembly_id = [a["id"] for a in assemblies if a["name"] == selected_assembly][0]
segments = supabase.table("segments").select("*").eq("assembly_id", assembly_id).execute().data
segment_names = [s["name"] for s in segments]
if segment_names:
    selected_segment = st.sidebar.selectbox("当前分段", segment_names)
else:
    selected_segment = None

# 菜单
page = st.sidebar.radio("功能", ["📤 上传分段数据", "📍 总组定位", "🧪 模拟匹配", "📖 查看历史总组"])

# ============================
# 页面1：上传分段数据
# ============================
if page == "📤 上传分段数据":
    st.header("上传分段理论与实测数据")
    with st.form("upload_form"):
        assem_name = st.text_input("总段名称（若新建）", value=selected_assembly)
        seg_name = st.text_input("分段名称")
        notes = st.text_area("分段备注")
        theo_file = st.file_uploader("理论值文件 (.txt)", type="txt")
        meas_file = st.file_uploader("实测值文件 (.txt)", type="txt")
        submitted = st.form_submit_button("上传并自动匹配")

    if submitted and seg_name and theo_file and meas_file:
        try:
            # 查找或创建总段
            res = supabase.table("assemblies").select("id").eq("name", assem_name).execute()
            if res.data:
                assem_id = res.data[0]["id"]
            else:
                ins = supabase.table("assemblies").insert({"name": assem_name, "notes": ""}).execute()
                assem_id = ins.data[0]["id"]

            # 插入分段
            seg = supabase.table("segments").insert({
                "assembly_id": assem_id, "name": seg_name, "notes": notes
            }).execute()
            seg_id = seg.data[0]["id"]

            # 解析数据
            theo_df = parse_txt(theo_file)
            meas_df = parse_txt(meas_file)

            # 存入理论点
            theo_records = []
            for _, row in theo_df.iterrows():
                theo_records.append({
                    "segment_id": seg_id,
                    "point_name": row["point_name"],
                    "x": float(row["x"]), "y": float(row["y"]), "z": float(row["z"])
                })
            supabase.table("theoretical_points").insert(theo_records).execute()

            # 存入实测点
            meas_records = []
            for _, row in meas_df.iterrows():
                meas_records.append({
                    "segment_id": seg_id,
                    "point_name": row["point_name"],
                    "x": float(row["x"]), "y": float(row["y"]), "z": float(row["z"])
                })
            supabase.table("measured_points").insert(meas_records).execute()

            # 自动匹配
            theo_xyz = theo_df[["x","y","z"]].to_numpy()
            meas_xyz = meas_df[["x","y","z"]].to_numpy()
            mask, idx, _ = match_points(theo_xyz, meas_xyz)

            # 获取插入后的ID（重新查询）
            theo_db = supabase.table("theoretical_points").select("id","point_name").eq("segment_id", seg_id).execute().data
            meas_db = supabase.table("measured_points").select("id","point_name").eq("segment_id", seg_id).execute().data
            theo_dict = {t["point_name"]: t["id"] for t in theo_db}
            meas_dict = {m["point_name"]: m["id"] for m in meas_db}

            pairs = []
            for i, matched in enumerate(mask):
                if matched:
                    theo_name = theo_df.iloc[i]["point_name"]
                    meas_name = meas_df.iloc[idx[i]]["point_name"]
                    pairs.append({
                        "segment_id": seg_id,
                        "theoretical_id": theo_dict[theo_name],
                        "measured_id": meas_dict[meas_name]
                    })
            if pairs:
                supabase.table("match_pairs").insert(pairs).execute()

            st.success(f"分段 {seg_name} 上传成功，共匹配 {len(pairs)} 对点")
            st.rerun()
        except Exception as e:
            st.error(f"上传失败: {e}")

# ============================
# 页面2：总组定位
# ============================
elif page == "📍 总组定位":
    st.header("总组定位点输入")
    if not selected_segment:
        st.warning("请先在侧边栏选择分段")
        st.stop()

    seg_data = next(s for s in segments if s["name"] == selected_segment)
    st.markdown(f"**当前分段**: {selected_segment}  |  备注: {seg_data['notes']}")

    # 获取当前分段的理论点和匹配的实测点
    theo_db = supabase.table("theoretical_points").select("id, point_name, x, y, z").eq("segment_id", seg_data["id"]).execute().data
    pairs_db = supabase.table("match_pairs").select("theoretical_id, measured_id").eq("segment_id", seg_data["id"]).execute().data
    meas_db = supabase.table("measured_points").select("id, point_name, x, y, z").eq("segment_id", seg_data["id"]).execute().data

    theo_lookup = {t["id"]: t for t in theo_db}
    meas_lookup = {m["id"]: m for m in meas_db}
    pair_map = {p["theoretical_id"]: p["measured_id"] for p in pairs_db}

    theo_xyz = np.array([[t["x"],t["y"],t["z"]] for t in theo_db])
    theo_ids = [t["id"] for t in theo_db]

    # 输入定位值
    pos_text = st.text_area("输入定位值（每行：点号,X,Y,Z 或 X,Y,Z）", height=150)
    if st.button("开始匹配") and pos_text:
        lines = pos_text.strip().split("\n")
        pos_points = []
        for line in lines:
            parts = line.strip().split(",")
            if len(parts) == 4:
                name, x, y, z = parts
            elif len(parts) == 3:
                name = ""
                x, y, z = parts
            else:
                continue
            pos_points.append({"name": name, "x": float(x), "y": float(y), "z": float(z)})

        pos_xyz = np.array([[p["x"],p["y"],p["z"]] for p in pos_points])

        # 对每个定位值，在当前分段理论点中找最近点
        tree = KDTree(theo_xyz)
        dist, idx = tree.query(pos_xyz)
        results = []
        matched_theo_ids = []
        for i, p in enumerate(pos_points):
            t_id = theo_ids[idx[i]]
            t = theo_lookup[t_id]
            # 偏差1：实测-理论（如果有匹配实测点）
            if t_id in pair_map:
                m = meas_lookup[pair_map[t_id]]
                dev1 = np.array([m["x"]-t["x"], m["y"]-t["y"], m["z"]-t["z"]])
            else:
                dev1 = np.array([0.0,0.0,0.0])  # 无实测值则偏差为0
            dev2 = np.array([p["x"]-t["x"], p["y"]-t["y"], p["z"]-t["z"]])
            results.append({
                "t_name": t["point_name"],
                "t_xyz": np.array([t["x"],t["y"],t["z"]]),
                "dev1": dev1,
                "dev2": dev2
            })
            matched_theo_ids.append(t_id)

        # 保存到 session 供模拟页面使用
        st.session_state.current_positions = pos_points
        st.session_state.current_matched_theo = matched_theo_ids

        # 显示文本格式
        for res in results:
            tx, ty, tz = res["t_xyz"]
            d1x, d1y, d1z = res["dev1"]
            d2x, d2y, d2z = res["dev2"]
            def fmt(val):
                return f"{'+' if val >=0 else ''}{int(val)}"
            st.markdown(f"**点号 {res['t_name']}**")
            st.markdown(f"X：{tx:.0f}（{fmt(d1x)}）【{fmt(d2x)}】")
            st.markdown(f"Y：{ty:.0f}（{fmt(d1y)}）【{fmt(d2y)}】")
            st.markdown(f"Z：{tz:.0f}（{fmt(d1z)}）【{fmt(d2z)}】")
            st.markdown("---")

        # 绘制三维图
        fig = go.Figure()
        # 理论点
        fig.add_trace(go.Scatter3d(
            x=theo_xyz[:,0], y=theo_xyz[:,1], z=theo_xyz[:,2],
            mode='markers', marker=dict(size=2, color='gray'), name='理论点'
        ))
        # 实测点（有匹配的）
        matched_meas = [meas_lookup[pair_map[tid]] for tid in theo_ids if tid in pair_map]
        if matched_meas:
            mx = [m["x"] for m in matched_meas]
            my = [m["y"] for m in matched_meas]
            mz = [m["z"] for m in matched_meas]
            fig.add_trace(go.Scatter3d(
                x=mx, y=my, z=mz, mode='markers',
                marker=dict(size=3, color='blue'), name='实测点'
            ))
        # 定位值点（红色，显示偏差）
        loc_text = []
        for res in results:
            loc_text.append(
                f"X:{res['t_xyz'][0]:.0f}({fmt(res['dev1'][0])})[{fmt(res['dev2'][0])}]<br>"
                f"Y:{res['t_xyz'][1]:.0f}({fmt(res['dev1'][1])})[{fmt(res['dev2'][1])}]<br>"
                f"Z:{res['t_xyz'][2]:.0f}({fmt(res['dev1'][2])})[{fmt(res['dev2'][2])}]"
            )
        fig.add_trace(go.Scatter3d(
            x=pos_xyz[:,0], y=pos_xyz[:,1], z=pos_xyz[:,2],
            mode='markers+text', text=loc_text, textposition='top center',
            marker=dict(size=8, color='red'), name='定位值'
        ))
        # 包围盒
        xe, ye, ze = get_box_edges(pos_xyz)
        fig.add_trace(go.Scatter3d(
            x=xe, y=ye, z=ze, mode='lines',
            line=dict(color='red', width=2), name='包围盒'
        ))
        fig.update_layout(scene=dict(aspectmode='data'), height=600)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"分段备注: {seg_data['notes']}")

    # 总组完成按钮
    if st.button("✅ 确认总组完成"):
        if st.session_state.current_positions is None:
            st.warning("请先进行定位值匹配")
        else:
            # 更新分段为已总组
            supabase.table("segments").update({"is_assembled": True}).eq("id", seg_data["id"]).execute()
            # 保存定位值
            pos_data = st.session_state.current_positions
            theo_ids = st.session_state.current_matched_theo
            for i, p in enumerate(pos_data):
                t = theo_lookup[theo_ids[i]]
                dev2 = np.array([p["x"]-t["x"], p["y"]-t["y"], p["z"]-t["z"]])
                if theo_ids[i] in pair_map:
                    m = meas_lookup[pair_map[theo_ids[i]]]
                    dev1 = np.array([m["x"]-t["x"], m["y"]-t["y"], m["z"]-t["z"]])
                else:
                    dev1 = np.zeros(3)
                supabase.table("assembly_positions").insert({
                    "segment_id": seg_data["id"],
                    "theoretical_id": theo_ids[i],
                    "position_x": p["x"], "position_y": p["y"], "position_z": p["z"],
                    "deviation_x": float(dev2[0]),
                    "deviation_y": float(dev2[1]),
                    "deviation_z": float(dev2[2]),
                    "measured_deviation_x": float(dev1[0]),
                    "measured_deviation_y": float(dev1[1]),
                    "measured_deviation_z": float(dev1[2])
                }).execute()
            st.success("总组完成，数据已保存")
            st.session_state.current_positions = None
            st.rerun()

# ============================
# 页面3：模拟匹配（用其他分段）
# ============================
elif page == "🧪 模拟匹配":
    st.header("模拟匹配（使用其他分段）")
    if st.session_state.current_positions is None:
        st.warning("请先在总组界面输入定位值并匹配")
        st.stop()
    if not selected_segment:
        st.stop()

    current_seg_id = [s for s in segments if s["name"] == selected_segment][0]["id"]
    other_segs = [s for s in segments if s["id"] != current_seg_id]

    if not other_segs:
        st.warning("当前总段没有其他分段")
        st.stop()

    selected_other = st.selectbox("选择要模拟的分段", [s["name"] for s in other_segs])
    other_seg = next(s for s in other_segs if s["name"] == selected_other)
    other_id = other_seg["id"]

    # 获取该分段的理论点
    theo_other = supabase.table("theoretical_points").select("id, point_name, x, y, z").eq("segment_id", other_id).execute().data
    if not theo_other:
        st.error("该分段无理论数据")
        st.stop()
    theo_o_xyz = np.array([[t["x"],t["y"],t["z"]] for t in theo_other])
    theo_o_ids = [t["id"] for t in theo_other]

    # 如果已总组，则从 assembly_positions 获取定位值代替实测值
    if other_seg["is_assembled"]:
        # 读取该分段的定位值记录
        ap = supabase.table("assembly_positions").select("*").eq("segment_id", other_id).execute().data
        # 构造一个“定位值作为实测”的字典
        pos_as_meas = {}
        for rec in ap:
            pos_as_meas[rec["theoretical_id"]] = {
                "x": rec["position_x"], "y": rec["position_y"], "z": rec["position_z"]
            }
    else:
        # 读取实测匹配
        pairs_o = supabase.table("match_pairs").select("theoretical_id, measured_id").eq("segment_id", other_id).execute().data
        meas_o = supabase.table("measured_points").select("id, x, y, z").eq("segment_id", other_id).execute().data
        meas_o_lookup = {m["id"]: m for m in meas_o}
        pair_map_o = {p["theoretical_id"]: p["measured_id"] for p in pairs_o}

    # 对每个定位值，找最近的理论点
    pos_list = st.session_state.current_positions
    pos_xyz = np.array([[p["x"],p["y"],p["z"]] for p in pos_list])
    tree = KDTree(theo_o_xyz)
    dist, idx = tree.query(pos_xyz)
    results = []
    for i, p in enumerate(pos_list):
        t = theo_other[idx[i]]
        # 偏差3：实测（或定位）-理论
        if other_seg["is_assembled"]:
            if t["id"] in pos_as_meas:
                pm = pos_as_meas[t["id"]]
                dev3 = np.array([pm["x"]-t["x"], pm["y"]-t["y"], pm["z"]-t["z"]])
            else:
                dev3 = np.zeros(3)
        else:
            if t["id"] in pair_map_o:
                m = meas_o_lookup[pair_map_o[t["id"]]]
                dev3 = np.array([m["x"]-t["x"], m["y"]-t["y"], m["z"]-t["z"]])
            else:
                dev3 = np.zeros(3)
        dev2 = np.array([p["x"]-t["x"], p["y"]-t["y"], p["z"]-t["z"]])
        results.append({
            "t_name": t["point_name"],
            "t_xyz": np.array([t["x"],t["y"],t["z"]]),
            "dev3": dev3,
            "dev2": dev2
        })

    # 显示文本
    for res in results:
        tx, ty, tz = res["t_xyz"]
        d3x, d3y, d3z = res["dev3"]
        d2x, d2y, d2z = res["dev2"]
        def fmt(v): return f"{'+' if v>=0 else ''}{int(v)}"
        st.markdown(f"**点号 {res['t_name']}**")
        st.markdown(f"X：{tx:.0f}（{fmt(d3x)}）【{fmt(d2x)}】")
        st.markdown(f"Y：{ty:.0f}（{fmt(d3y)}）【{fmt(d2y)}】")
        st.markdown(f"Z：{tz:.0f}（{fmt(d3z)}）【{fmt(d2z)}】")
        st.markdown("---")

    # 三维图（定位值用红色，其他分段理论点用绿色，匹配点用黄色）
    fig = go.Figure()
    fig.add_trace(go.Scatter3d(x=theo_o_xyz[:,0], y=theo_o_xyz[:,1], z=theo_o_xyz[:,2],
                               mode='markers', marker=dict(size=2, color='lightgreen'), name='其他分段理论点'))
    # 显示匹配到的点（理论点位置用黄色大点）
    matched_xyz = np.array([res["t_xyz"] for res in results])
    fig.add_trace(go.Scatter3d(x=matched_xyz[:,0], y=matched_xyz[:,1], z=matched_xyz[:,2],
                               mode='markers', marker=dict(size=6, color='yellow'), name='匹配理论点'))
    # 定位值
    loc_text = []
    for res in results:
        loc_text.append(
            f"X:{res['t_xyz'][0]:.0f}({fmt(res['dev3'][0])})[{fmt(res['dev2'][0])}]<br>"
            f"Y:{res['t_xyz'][1]:.0f}({fmt(res['dev3'][1])})[{fmt(res['dev2'][1])}]<br>"
            f"Z:{res['t_xyz'][2]:.0f}({fmt(res['dev3'][2])})[{fmt(res['dev2'][2])}]"
        )
    fig.add_trace(go.Scatter3d(x=pos_xyz[:,0], y=pos_xyz[:,1], z=pos_xyz[:,2],
                               mode='markers+text', text=loc_text, textposition='top center',
                               marker=dict(size=8, color='red'), name='定位值'))
    xe, ye, ze = get_box_edges(pos_xyz)
    fig.add_trace(go.Scatter3d(x=xe, y=ye, z=ze, mode='lines', line=dict(color='red', width=2)))
    fig.update_layout(scene=dict(aspectmode='data'), height=600)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"模拟分段备注: {other_seg['notes']}")

# ============================
# 页面4：查看历史已总组分段
# ============================
elif page == "📖 查看历史总组":
    st.header("查看已总组分段定位数据")
    finished_segs = [s for s in segments if s["is_assembled"]]
    if not finished_segs:
        st.info("暂无已总组分段")
        st.stop()
    hist_seg = st.selectbox("选择已总组分段", [s["name"] for s in finished_segs])
    seg = next(s for s in finished_segs if s["name"] == hist_seg)
    ap_data = supabase.table("assembly_positions").select("*, theoretical_points(x,y,z,point_name)").eq("segment_id", seg["id"]).execute().data
    theo_data = supabase.table("theoretical_points").select("*").eq("segment_id", seg["id"]).execute().data

    st.markdown(f"**分段**: {hist_seg}  |  备注: {seg['notes']}")
    pos_xyz_list = []
    loc_text_list = []
    for rec in ap_data:
        t = rec["theoretical_points"]
        dev1 = np.array([rec["measured_deviation_x"], rec["measured_deviation_y"], rec["measured_deviation_z"]])
        dev2 = np.array([rec["deviation_x"], rec["deviation_y"], rec["deviation_z"]])
        def fmt(v): return f"{'+' if v>=0 else ''}{int(v)}"
        st.markdown(f"**点号 {t['point_name']}**")
        st.markdown(f"X：{t['x']:.0f}（{fmt(dev1[0])}）【{fmt(dev2[0])}】")
        st.markdown(f"Y：{t['y']:.0f}（{fmt(dev1[1])}）【{fmt(dev2[1])}】")
        st.markdown(f"Z：{t['z']:.0f}（{fmt(dev1[2])}）【{fmt(dev2[2])}】")
        st.markdown("---")
        pos_xyz_list.append([rec["position_x"], rec["position_y"], rec["position_z"]])
        loc_text_list.append(
            f"X:{t['x']:.0f}({fmt(dev1[0])})[{fmt(dev2[0])}]<br>"
            f"Y:{t['y']:.0f}({fmt(dev1[1])})[{fmt(dev2[1])}]<br>"
            f"Z:{t['z']:.0f}({fmt(dev1[2])})[{fmt(dev2[2])}]"
        )
    # 三维图
    if pos_xyz_list:
        pos_xyz_arr = np.array(pos_xyz_list)
        theo_all_xyz = np.array([[t["x"],t["y"],t["z"]] for t in theo_data])
        fig = go.Figure()
        fig.add_trace(go.Scatter3d(x=theo_all_xyz[:,0], y=theo_all_xyz[:,1], z=theo_all_xyz[:,2],
                                   mode='markers', marker=dict(size=2, color='gray'), name='理论点'))
        fig.add_trace(go.Scatter3d(x=pos_xyz_arr[:,0], y=pos_xyz_arr[:,1], z=pos_xyz_arr[:,2],
                                   mode='markers+text', text=loc_text_list, textposition='top center',
                                   marker=dict(size=8, color='red'), name='定位值'))
        xe, ye, ze = get_box_edges(pos_xyz_arr)
        fig.add_trace(go.Scatter3d(x=xe, y=ye, z=ze, mode='lines', line=dict(color='red', width=2)))
        fig.update_layout(scene=dict(aspectmode='data'), height=600)
        st.plotly_chart(fig, use_container_width=True)