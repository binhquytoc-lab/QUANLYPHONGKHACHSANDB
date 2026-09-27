import os
import socket
from datetime import date, datetime

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

# ==============================================================================
# CẤU HÌNH CHUNG
# ==============================================================================

MAX_ROOMS = 100      # Giới hạn tối đa số phòng có thể quản lý
DEFAULT_ROOMS = 20   # Số phòng mặc định khi khởi tạo database lần đầu

ROOM_STATUSES = ["Trống", "Đang sử dụng", "Đang dọn dẹp", "Bảo trì"]
ROOM_TYPES = ["Đơn", "Đôi", "Suite", "Gia đình", "VIP"]
STATUS_ICON = {
    "Trống": "🟢",
    "Đang sử dụng": "🔴",
    "Đang dọn dẹp": "🟡",
    "Bảo trì": "⚫",
}

st.set_page_config(page_title="Quản lý Khách sạn", page_icon="🏨", layout="wide")

if os.path.exists("VT.jpg"):
    st.image("VT.jpg")

# ==============================================================================
# KẾT NỐI AIVEN MYSQL (cấu hình trực tiếp)
# ==============================================================================
# Thay các giá trị bên dưới bằng thông tin database Aiven của bạn.

DB_USER = "avnadmin" # SỬA LẠI USER
DB_PASSWORD = "AVNS_TX2oBXmTGGjXba6p7j1" # SỬA LẠI PASSWORD
DB_HOST = "mysql-3a5ef2bc-binhquytoc.a.aivencloud.com" # SỬA LẠI HOST
DB_PORT = 14483 # SỬA LẠI PORT
DB_NAME = "hotel_management"

# Làm sạch dữ liệu kết nối
DB_USER = str(DB_USER).strip()
DB_PASSWORD = str(DB_PASSWORD).strip()
DB_HOST = str(DB_HOST).strip()
DB_NAME = str(DB_NAME).strip()
DB_PORT = int(DB_PORT)

with st.sidebar.expander("🔧 Kiểm tra kết nối Aiven", expanded=False):
    st.write("**HOST:**", repr(DB_HOST))
    st.write("**PORT:**", repr(DB_PORT))
    st.write("**DATABASE:**", repr(DB_NAME))
    st.write("**USER:**", repr(DB_USER))

    if st.button("🔍 Kiểm tra DNS Aiven"):
        try:
            ip_address = socket.gethostbyname(DB_HOST)
            st.success(f"DNS OK - Host Aiven trỏ tới IP: {ip_address}")
        except Exception as e:
            st.error(f"DNS ERROR: Không phân giải được hostname Aiven.\n\n{e}")

DATABASE_URL = URL.create(
    drivername="mysql+pymysql",
    username=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=DB_PORT,
    database=DB_NAME,
)


@st.cache_resource
def get_db_engine():
    return create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 15},
        pool_size=5,
        max_overflow=5,
    )


# ==============================================================================
# KHỞI TẠO BẢNG & DỮ LIỆU MẶC ĐỊNH
# ==============================================================================

def init_db():
    engine = get_db_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS rooms (
                id INT AUTO_INCREMENT PRIMARY KEY,
                room_number VARCHAR(20) NOT NULL UNIQUE,
                room_type VARCHAR(50) NOT NULL,
                price DECIMAL(12, 2) NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'Trống',
                note VARCHAR(255)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                room_id INT NOT NULL,
                guest_name VARCHAR(150) NOT NULL,
                phone VARCHAR(30),
                check_in DATE NOT NULL,
                check_out DATE NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'Đang ở',
                created_at DATETIME NOT NULL,
                total_amount DECIMAL(14, 2) NULL,
                FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        )

        # Nếu bảng bookings được tạo từ phiên bản cũ (chưa có cột total_amount) thì bổ sung
        try:
            conn.exec_driver_sql("ALTER TABLE bookings ADD COLUMN total_amount DECIMAL(14, 2) NULL")
        except Exception:
            pass  # Cột đã tồn tại

        # Nếu bảng rooms đang trống, tạo sẵn DEFAULT_ROOMS phòng mặc định
        count = conn.execute(text("SELECT COUNT(*) FROM rooms")).scalar()
        if count == 0:
            rows = [
                {"room_number": str(101 + i), "room_type": "Đơn", "price": 400000, "status": "Trống", "note": ""}
                for i in range(DEFAULT_ROOMS)
            ]
            conn.execute(
                text(
                    "INSERT INTO rooms (room_number, room_type, price, status, note) "
                    "VALUES (:room_number, :room_type, :price, :status, :note)"
                ),
                rows,
            )


db_ready = False
try:
    init_db()
    db_ready = True
except Exception as e:
    st.error("❌ Không thể kết nối hoặc khởi tạo Aiven MySQL.")
    st.code(str(e), language="text")
    st.warning("Kiểm tra lại HOST, PORT, USER, PASSWORD, DATABASE, và whitelist IP trên Aiven nếu có bật.")
    st.stop()


# ==============================================================================
# HÀM TRUY VẤN (PHÒNG)
# ==============================================================================

def get_rooms_df():
    engine = get_db_engine()
    return pd.read_sql("SELECT * FROM rooms ORDER BY room_number", engine)


def room_count():
    engine = get_db_engine()
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM rooms")).scalar()


def room_number_exists(room_number, exclude_id=None):
    engine = get_db_engine()
    with engine.connect() as conn:
        if exclude_id is None:
            q = text("SELECT COUNT(*) FROM rooms WHERE room_number = :rn")
            return conn.execute(q, {"rn": room_number}).scalar() > 0
        q = text("SELECT COUNT(*) FROM rooms WHERE room_number = :rn AND id != :id")
        return conn.execute(q, {"rn": room_number, "id": exclude_id}).scalar() > 0


def add_room(room_number, room_type, price, status, note):
    if room_count() >= MAX_ROOMS:
        return False, f"Đã đạt giới hạn tối đa {MAX_ROOMS} phòng. Không thể thêm phòng mới."
    if room_number_exists(room_number):
        return False, "Số phòng này đã tồn tại."
    engine = get_db_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO rooms (room_number, room_type, price, status, note) "
                "VALUES (:room_number, :room_type, :price, :status, :note)"
            ),
            {"room_number": room_number, "room_type": room_type, "price": price, "status": status, "note": note},
        )
    return True, "Thêm phòng thành công."


def update_room(room_id, room_number, room_type, price, status, note):
    if room_number_exists(room_number, exclude_id=room_id):
        return False, "Số phòng này đã tồn tại."
    engine = get_db_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE rooms SET room_number=:room_number, room_type=:room_type, "
                "price=:price, status=:status, note=:note WHERE id=:id"
            ),
            {
                "room_number": room_number,
                "room_type": room_type,
                "price": price,
                "status": status,
                "note": note,
                "id": room_id,
            },
        )
    return True, "Cập nhật phòng thành công."


def delete_room(room_id):
    engine = get_db_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM rooms WHERE id=:id"), {"id": room_id})


def set_room_status(room_id, status):
    engine = get_db_engine()
    with engine.begin() as conn:
        conn.execute(text("UPDATE rooms SET status=:status WHERE id=:id"), {"status": status, "id": room_id})


def generate_rooms(count, start_number, room_type, price, replace=True):
    """Tạo `count` phòng (tối đa MAX_ROOMS), đánh số liên tiếp từ start_number."""
    count = max(1, min(count, MAX_ROOMS))
    engine = get_db_engine()

    with engine.begin() as conn:
        if replace:
            conn.exec_driver_sql("DELETE FROM bookings")
            conn.exec_driver_sql("DELETE FROM rooms")
            rows = [
                {
                    "room_number": str(start_number + i),
                    "room_type": room_type,
                    "price": price,
                    "status": "Trống",
                    "note": "",
                }
                for i in range(count)
            ]
            conn.execute(
                text(
                    "INSERT INTO rooms (room_number, room_type, price, status, note) "
                    "VALUES (:room_number, :room_type, :price, :status, :note)"
                ),
                rows,
            )
            return count
        else:
            existing = {r for (r,) in conn.execute(text("SELECT room_number FROM rooms"))}
            remaining_slots = MAX_ROOMS - len(existing)
            rows = []
            for i in range(count):
                if len(rows) >= remaining_slots:
                    break
                rn = str(start_number + i)
                if rn in existing:
                    continue
                rows.append(
                    {"room_number": rn, "room_type": room_type, "price": price, "status": "Trống", "note": ""}
                )
            if rows:
                conn.execute(
                    text(
                        "INSERT INTO rooms (room_number, room_type, price, status, note) "
                        "VALUES (:room_number, :room_type, :price, :status, :note)"
                    ),
                    rows,
                )
            return len(rows)


# ==============================================================================
# HÀM TRUY VẤN (ĐẶT PHÒNG)
# ==============================================================================

def add_booking(room_id, guest_name, phone, check_in, check_out):
    engine = get_db_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO bookings (room_id, guest_name, phone, check_in, check_out, status, created_at) "
                "VALUES (:room_id, :guest_name, :phone, :check_in, :check_out, 'Đang ở', :created_at)"
            ),
            {
                "room_id": room_id,
                "guest_name": guest_name,
                "phone": phone,
                "check_in": check_in,
                "check_out": check_out,
                "created_at": datetime.now(),
            },
        )
        conn.execute(text("UPDATE rooms SET status='Đang sử dụng' WHERE id=:id"), {"id": room_id})


def calc_nights(check_in, check_out):
    """Tính số đêm ở, tối thiểu 1 đêm."""
    if isinstance(check_in, str):
        check_in = datetime.strptime(check_in, "%Y-%m-%d").date()
    if isinstance(check_out, str):
        check_out = datetime.strptime(check_out, "%Y-%m-%d").date()
    nights = (check_out - check_in).days
    return max(nights, 1)


def checkout_booking(booking_id, room_id, total_amount):
    """Trả phòng và lưu lại số tiền cần thanh toán vào lịch sử đặt phòng."""
    engine = get_db_engine()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE bookings SET status='Đã trả phòng', total_amount=:amount WHERE id=:id"),
            {"amount": total_amount, "id": booking_id},
        )
        conn.execute(text("UPDATE rooms SET status='Đang dọn dẹp' WHERE id=:id"), {"id": room_id})


def get_bookings_df(active_only=False):
    engine = get_db_engine()
    query = """
        SELECT b.id, r.room_number, r.price, b.guest_name, b.phone, b.check_in, b.check_out,
               b.status, b.room_id, b.total_amount
        FROM bookings b
        JOIN rooms r ON b.room_id = r.id
    """
    if active_only:
        query += " WHERE b.status = 'Đang ở'"
    query += " ORDER BY b.check_in DESC"
    return pd.read_sql(query, engine)


# ==============================================================================
# GIAO DIỆN
# ==============================================================================

def main():
    st.title("🏨 Hệ thống Quản lý Phòng Khách sạn")
    st.caption(
        f"✅ Dữ liệu được lưu trực tiếp vào MySQL (Aiven). "
        f"Mặc định **{DEFAULT_ROOMS} phòng**, quản lý tối đa **{MAX_ROOMS} phòng**."
    )

    tab_dashboard, tab_setup, tab_rooms, tab_add_room, tab_booking, tab_history = st.tabs(
        [
            "📊 Tổng quan",
            "⚙️ Thiết lập số phòng",
            "🛏️ Danh sách phòng",
            "➕ Thêm/Sửa phòng",
            "📅 Đặt phòng / Trả phòng",
            "📖 Lịch sử đặt phòng",
        ]
    )

    # ---------------- TAB: TỔNG QUAN ----------------
    with tab_dashboard:
        rooms_df = get_rooms_df()
        total = len(rooms_df)
        occupied = len(rooms_df[rooms_df["status"] == "Đang sử dụng"]) if total else 0
        empty = len(rooms_df[rooms_df["status"] == "Trống"]) if total else 0
        cleaning = len(rooms_df[rooms_df["status"] == "Đang dọn dẹp"]) if total else 0
        maintenance = len(rooms_df[rooms_df["status"] == "Bảo trì"]) if total else 0

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Tổng số phòng", f"{total}/{MAX_ROOMS}")
        c2.metric("Đang sử dụng", occupied)
        c3.metric("Phòng trống", empty)
        c4.metric("Đang dọn dẹp", cleaning)
        c5.metric("Đang bảo trì", maintenance)

        st.progress(total / MAX_ROOMS if MAX_ROOMS else 0, text=f"Đã sử dụng {total}/{MAX_ROOMS} phòng")

        st.subheader("Trạng thái các phòng")
        if total == 0:
            st.info("Chưa có phòng nào. Hãy thiết lập số phòng ở tab '⚙️ Thiết lập số phòng'.")
        else:
            cols = st.columns(6)
            for i, row in rooms_df.iterrows():
                with cols[i % 6]:
                    st.markdown(
                        f"**{STATUS_ICON.get(row['status'], '⚪')} Phòng {row['room_number']}**\n\n"
                        f"{row['room_type']}\n\n"
                        f"{row['status']}"
                    )

    # ---------------- TAB: THIẾT LẬP SỐ PHÒNG ----------------
    with tab_setup:
        st.subheader("Thiết lập số lượng phòng")
        st.caption(f"Chọn số lượng phòng muốn quản lý (từ 1 đến {MAX_ROOMS}). Hệ thống sẽ tự động tạo lại danh sách phòng trong database.")

        current_total = room_count()

        with st.form("setup_rooms_form"):
            c1, c2 = st.columns(2)
            with c1:
                room_count_input = st.number_input(
                    "Số lượng phòng",
                    min_value=1,
                    max_value=MAX_ROOMS,
                    value=min(current_total or DEFAULT_ROOMS, MAX_ROOMS),
                )
                start_number = st.number_input("Số phòng bắt đầu (VD: 101)", min_value=1, value=101, step=1)
            with c2:
                default_type = st.selectbox("Loại phòng mặc định", ROOM_TYPES)
                default_price = st.number_input("Giá mặc định (VNĐ/đêm)", min_value=0.0, value=400000.0, step=50000.0)

            mode = st.radio(
                "Chế độ tạo phòng",
                ["Tạo mới toàn bộ (xóa danh sách cũ)", "Thêm vào danh sách hiện có"],
                horizontal=False,
            )

            submitted = st.form_submit_button("⚙️ Áp dụng thiết lập")
            if submitted:
                replace = mode.startswith("Tạo mới")
                if not replace and current_total + room_count_input > MAX_ROOMS:
                    st.error(
                        f"Không thể thêm {room_count_input} phòng vì tổng số phòng sẽ vượt quá {MAX_ROOMS}. "
                        f"Hiện có {current_total} phòng, chỉ có thể thêm tối đa {MAX_ROOMS - current_total} phòng."
                    )
                else:
                    created = generate_rooms(room_count_input, start_number, default_type, default_price, replace=replace)
                    st.success(f"Đã thiết lập {created} phòng thành công (đánh số từ {start_number}).")
                    st.rerun()

        st.info(f"Hiện tại đang quản lý **{current_total}/{MAX_ROOMS}** phòng trong database.")

    # ---------------- TAB: DANH SÁCH PHÒNG ----------------
    with tab_rooms:
        st.subheader("Danh sách phòng")
        rooms_df = get_rooms_df()

        filter_status = st.selectbox("Lọc theo trạng thái", ["Tất cả"] + ROOM_STATUSES)
        display_df = rooms_df if (filter_status == "Tất cả" or rooms_df.empty) else rooms_df[rooms_df["status"] == filter_status]

        if display_df.empty:
            st.info("Không có phòng nào phù hợp.")
        else:
            st.dataframe(
                display_df[["room_number", "room_type", "price", "status", "note"]].rename(
                    columns={
                        "room_number": "Số phòng",
                        "room_type": "Loại phòng",
                        "price": "Giá (VNĐ/đêm)",
                        "status": "Trạng thái",
                        "note": "Ghi chú",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

        st.divider()
        st.subheader("Cập nhật / Xóa phòng")
        if not rooms_df.empty:
            room_options = {f"Phòng {r['room_number']} ({r['room_type']})": r["id"] for _, r in rooms_df.iterrows()}
            selected_label = st.selectbox("Chọn phòng", list(room_options.keys()))
            selected_id = int(room_options[selected_label])
            room_row = rooms_df[rooms_df["id"] == selected_id].iloc[0]

            with st.form("edit_room_form"):
                c1, c2 = st.columns(2)
                with c1:
                    new_number = st.text_input("Số phòng", value=room_row["room_number"])
                    new_type = st.selectbox(
                        "Loại phòng", ROOM_TYPES,
                        index=ROOM_TYPES.index(room_row["room_type"]) if room_row["room_type"] in ROOM_TYPES else 0,
                    )
                with c2:
                    new_price = st.number_input("Giá/đêm (VNĐ)", min_value=0.0, value=float(room_row["price"]), step=50000.0)
                    new_status = st.selectbox("Trạng thái", ROOM_STATUSES, index=ROOM_STATUSES.index(room_row["status"]))
                new_note = st.text_area("Ghi chú", value=room_row["note"] or "")

                col_save, col_delete = st.columns(2)
                submitted = col_save.form_submit_button("💾 Lưu thay đổi", use_container_width=True)
                deleted = col_delete.form_submit_button("🗑️ Xóa phòng", use_container_width=True)

                if submitted:
                    ok, msg = update_room(selected_id, new_number.strip(), new_type, new_price, new_status, new_note)
                    st.success(msg) if ok else st.error(msg)
                    if ok:
                        st.rerun()

                if deleted:
                    delete_room(selected_id)
                    st.success("Đã xóa phòng.")
                    st.rerun()
        else:
            st.info("Chưa có phòng nào để chỉnh sửa.")

    # ---------------- TAB: THÊM PHÒNG ----------------
    with tab_add_room:
        st.subheader("Thêm phòng mới")
        current_total = room_count()
        st.caption(f"Đang có {current_total}/{MAX_ROOMS} phòng.")

        if current_total >= MAX_ROOMS:
            st.warning(f"Đã đạt giới hạn tối đa {MAX_ROOMS} phòng. Vui lòng xóa bớt phòng hoặc dùng tab '⚙️ Thiết lập số phòng' để tạo lại.")

        with st.form("add_room_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                room_number = st.text_input("Số phòng (VD: 101)")
                room_type = st.selectbox("Loại phòng", ROOM_TYPES)
            with c2:
                price = st.number_input("Giá/đêm (VNĐ)", min_value=0.0, step=50000.0, value=500000.0)
                status = st.selectbox("Trạng thái ban đầu", ROOM_STATUSES)
            note = st.text_area("Ghi chú (tùy chọn)")

            submitted = st.form_submit_button("➕ Thêm phòng", disabled=current_total >= MAX_ROOMS)
            if submitted:
                if not room_number.strip():
                    st.error("Vui lòng nhập số phòng.")
                else:
                    ok, msg = add_room(room_number.strip(), room_type, price, status, note)
                    st.success(msg) if ok else st.error(msg)

    # ---------------- TAB: ĐẶT PHÒNG / TRẢ PHÒNG ----------------
    with tab_booking:
        # Nếu vừa trả phòng ở lượt chạy trước, hiển thị hóa đơn thanh toán tại đây
        if "last_receipt" in st.session_state:
            receipt = st.session_state["last_receipt"]
            with st.container(border=True):
                st.subheader("🧾 Hóa đơn thanh toán")
                st.markdown(
                    f"- **Phòng:** {receipt['room_number']}\n"
                    f"- **Khách hàng:** {receipt['guest_name']}\n"
                    f"- **Nhận phòng:** {receipt['check_in']}\n"
                    f"- **Trả phòng:** {receipt['check_out']}\n"
                    f"- **Số đêm:** {receipt['nights']} đêm\n"
                    f"- **Giá phòng:** {receipt['price']:,.0f}đ/đêm\n"
                )
                st.success(f"💰 **TỔNG TIỀN CẦN THANH TOÁN: {receipt['total']:,.0f} VNĐ**")
                if st.button("Đóng hóa đơn"):
                    del st.session_state["last_receipt"]
                    st.rerun()
            st.divider()

        st.subheader("Đặt phòng mới")
        rooms_df = get_rooms_df()
        available_rooms = rooms_df[rooms_df["status"] == "Trống"] if not rooms_df.empty else rooms_df

        if available_rooms.empty:
            st.warning("Hiện không có phòng trống nào.")
        else:
            with st.form("booking_form", clear_on_submit=True):
                room_options = {
                    f"Phòng {r['room_number']} ({r['room_type']}) - {float(r['price']):,.0f}đ/đêm": r["id"]
                    for _, r in available_rooms.iterrows()
                }
                selected_label = st.selectbox("Chọn phòng", list(room_options.keys()))
                guest_name = st.text_input("Tên khách hàng")
                phone = st.text_input("Số điện thoại")
                c1, c2 = st.columns(2)
                check_in = c1.date_input("Ngày nhận phòng", value=date.today())
                check_out = c2.date_input("Ngày trả phòng", value=date.today())

                submitted = st.form_submit_button("📅 Đặt phòng")
                if submitted:
                    if not guest_name.strip():
                        st.error("Vui lòng nhập tên khách hàng.")
                    elif check_out <= check_in:
                        st.error("Ngày trả phòng phải sau ngày nhận phòng.")
                    else:
                        add_booking(int(room_options[selected_label]), guest_name.strip(), phone.strip(), check_in, check_out)
                        st.success("Đặt phòng thành công!")
                        st.rerun()

        st.divider()
        st.subheader("Trả phòng")
        active_df = get_bookings_df(active_only=True)
        if active_df.empty:
            st.info("Không có phòng nào đang được sử dụng.")
        else:
            for _, b in active_df.iterrows():
                nights = calc_nights(b["check_in"], b["check_out"])
                estimated_total = nights * float(b["price"])
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 2, 1])
                    c1.markdown(f"**Phòng {b['room_number']}** — {b['guest_name']} ({b['phone']})")
                    c2.markdown(
                        f"Nhận: {b['check_in']} → Trả: {b['check_out']} "
                        f"({nights} đêm · {estimated_total:,.0f}đ)"
                    )
                    if c3.button("✅ Trả phòng", key=f"checkout_{b['id']}"):
                        checkout_booking(int(b["id"]), int(b["room_id"]), estimated_total)
                        st.session_state["last_receipt"] = {
                            "room_number": b["room_number"],
                            "guest_name": b["guest_name"],
                            "check_in": b["check_in"],
                            "check_out": b["check_out"],
                            "nights": nights,
                            "price": float(b["price"]),
                            "total": estimated_total,
                        }
                        st.rerun()

    # ---------------- TAB: LỊCH SỬ ----------------
    with tab_history:
        st.subheader("Lịch sử đặt phòng")
        history_df = get_bookings_df()
        if history_df.empty:
            st.info("Chưa có lịch sử đặt phòng.")
        else:
            display_history = history_df.copy()
            display_history["total_amount"] = display_history["total_amount"].fillna(0)
            st.dataframe(
                display_history[
                    ["room_number", "guest_name", "phone", "check_in", "check_out", "status", "total_amount"]
                ].rename(
                    columns={
                        "room_number": "Số phòng",
                        "guest_name": "Khách hàng",
                        "phone": "SĐT",
                        "check_in": "Nhận phòng",
                        "check_out": "Trả phòng",
                        "status": "Trạng thái",
                        "total_amount": "Số tiền đã thanh toán (VNĐ)",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

            total_revenue = display_history["total_amount"].sum()
            st.metric("💰 Tổng doanh thu đã thu (các lượt đã trả phòng)", f"{total_revenue:,.0f} VNĐ")


if __name__ == "__main__":
    main()
