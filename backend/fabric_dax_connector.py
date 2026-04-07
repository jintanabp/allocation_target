"""
fabric_dax_connector.py
────────────────────────────────────────────────────────────────────
เชื่อมต่อ Power BI / Microsoft Fabric ผ่าน DAX REST API

Key format ใน Power BI executeQueries response:
  - SUMMARIZECOLUMNS dimension cols  → "TableName[ColName]"
  - SUMMARIZECOLUMNS measure aliases → "[alias]"
  - SELECTCOLUMNS aliases            → "[alias]"
"""

import msal
import requests
import pandas as pd
import os
import atexit
import json


class FabricDAXConnector:
    def __init__(self):
        # Production: ตั้งค่า FABRIC_CLIENT_ID, FABRIC_TENANT_ID, FABRIC_DATASET_ID ใน environment
        self.client_id  = os.environ.get("FABRIC_CLIENT_ID", "d0d1f812-d677-490e-a9df-25c00baea1ab")
        self.tenant_id  = os.environ.get("FABRIC_TENANT_ID", "e442d6a7-a8dc-4ac8-880b-d272b11642e9")
        self.dataset_id = os.environ.get("FABRIC_DATASET_ID", "fac4dff8-9c2f-45fe-8971-ab2c429bea80")
        self.authority  = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.scope      = ["https://analysis.windows.net/powerbi/api/.default"]

        os.makedirs("data", exist_ok=True)
        self.cache_file = "data/token_cache.bin"
        self.cache      = msal.SerializableTokenCache()

        if os.path.exists(self.cache_file):
            with open(self.cache_file, "r") as f:
                self.cache.deserialize(f.read())

        atexit.register(self._save_cache)

        self.app = msal.PublicClientApplication(
            self.client_id,
            authority=self.authority,
            token_cache=self.cache,
        )

    # ──────────────────────────────────────────────
    # Auth / cache
    # ──────────────────────────────────────────────
    def _save_cache(self):
        if self.cache.has_state_changed:
            with open(self.cache_file, "w") as f:
                f.write(self.cache.serialize())

    def _get_access_token(self) -> str:
        accounts = self.app.get_accounts()
        if accounts:
            result = self.app.acquire_token_silent(self.scope, account=accounts[0])
        else:
            print("⏳ ไม่พบ token กำลังเปิดเบราว์เซอร์ให้ Login...")
            result = self.app.acquire_token_interactive(scopes=self.scope)

        if "access_token" in result:
            self._save_cache()
            return result["access_token"]
        raise Exception(f"❌ Login ไม่สำเร็จ: {result.get('error_description')}")

    # ──────────────────────────────────────────────
    # Core DAX executor
    # ──────────────────────────────────────────────
    def _execute_dax(self, dax_query: str, debug: bool = False) -> list[dict]:
        """
        ส่ง DAX query ไปยัง Power BI REST API แล้วคืน list of row dicts
        debug=True → print first row เพื่อดู key format จริงๆ
        """
        token = self._get_access_token()
        url = (
            f"https://api.powerbi.com/v1.0/myorg/datasets/"
            f"{self.dataset_id}/executeQueries"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "queries": [{"query": dax_query}],
            "serializerSettings": {"includeNulls": False},
        }
        response = requests.post(url, headers=headers, json=payload, timeout=60)

        if response.status_code != 200:
            raise Exception(
                f"❌ DAX query failed (HTTP {response.status_code}): {response.text[:600]}"
            )

        data = response.json()
        rows = data["results"][0]["tables"][0].get("rows", [])

        if debug and rows:
            print(f"🔍 DEBUG keys: {list(rows[0].keys())}")
            print(f"🔍 DEBUG first row: {json.dumps(rows[0], ensure_ascii=False)[:300]}")
        elif debug:
            print("🔍 DEBUG: query returned 0 rows")

        return rows

    # ──────────────────────────────────────────────
    # Safe key lookup — ลอง candidate keys หลายแบบ
    # ──────────────────────────────────────────────
    @staticmethod
    def _get(row: dict, *candidates, default=None):
        for k in candidates:
            if k in row:
                return row[k]
        return default

    # ──────────────────────────────────────────────
    # Date filter builders
    # ──────────────────────────────────────────────
    @staticmethod
    def _prev_months(target_month: int, target_year: int, n: int = 3) -> list[tuple[int, int]]:
        """คืน [(month, year), ...] ย้อนหลัง n เดือน (ข้ามเดือนปัจจุบัน 1 เดือน)"""
        result = []
        m, y = target_month, target_year
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        for _ in range(n):
            m -= 1
            if m == 0:
                m, y = 12, y - 1
            result.append((m, y))
        return result

    @staticmethod
    def _dax_date_filter(month_year_list: list[tuple[int, int]]) -> str:
        by_year: dict[int, list[int]] = {}
        for m, y in month_year_list:
            by_year.setdefault(y, []).append(m)
        parts = []
        for y, months in sorted(by_year.items()):
            ms = sorted(months)
            if len(ms) == 1:
                parts.append(f"(YEAR('DimDate'[Date]) = {y} && MONTH('DimDate'[Date]) = {ms[0]})")
            else:
                m_str = ", ".join(str(x) for x in ms)
                parts.append(f"(YEAR('DimDate'[Date]) = {y} && MONTH('DimDate'[Date]) IN {{{m_str}}})")
        return " || ".join(parts)

    @staticmethod
    def _dax_month_filter(month: int, year: int) -> str:
        return f"YEAR('DimDate'[Date]) = {year} && MONTH('DimDate'[Date]) = {month}"

    # ──────────────────────────────────────────────
    # TREATAS filter builders
    # ──────────────────────────────────────────────
    @staticmethod
    def _sku_treatas(sku_list: list) -> str:
        if not sku_list:
            return ""
        s = ", ".join(f'"{x}"' for x in sku_list)
        return f"TREATAS({{{s}}}, 'cross_sold_history_2y_qu'[ProductCode]),"

    @staticmethod
    def _emp_treatas(emp_list: list) -> str:
        if not emp_list:
            return ""
        s = ", ".join(f'"{x}"' for x in emp_list)
        return f"TREATAS({{{s}}}, 'cross_sold_history_2y_qu'[SalesmanCode]),"

    # ══════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════

    # ── 0. ดึง SuperCode ทั้งหมด (สำหรับ Dropdown) ───────────────────
    def get_all_super_codes(self) -> list[str]:
        """ดึงรายการ SuperCode ที่ไม่ซ้ำกันทั้งหมดจาก dim_salesman"""
        print("📡 [dim_salesman] กำลังดึงรายชื่อ SuperCode ทั้งหมด...")
        dax = """
EVALUATE
SUMMARIZECOLUMNS(
    'dim_salesman'[SuperCode]
)
"""
        rows = self._execute_dax(dax)
        codes = set()
        for r in rows:
            sc = str(self._get(r, "[SuperCode]", "dim_salesman[SuperCode]", default="")).strip()
            # กรองค่าว่าง หรือค่าที่ไม่ถูกต้องทิ้ง
            if sc and sc.upper() != "NONE" and sc != "0":
                codes.add(sc.upper())
                
        sorted_codes = sorted(list(codes))
        print(f"✅ พบ SuperCode ทั้งหมด {len(sorted_codes)} รหัส")
        return sorted_codes

    # ── 1. พนักงานจาก SuperCode ─────────────────────────────────────
    def get_employees_by_manager(self, manager_code: str) -> pd.DataFrame:
        """
        ดึงรายชื่อพนักงานจาก dim_salesman โดย SuperCode 
        (เพิ่ม TRIM และ UPPER เพื่อป้องกันปัญหาเคาะวรรคซ่อนอยู่ในฐานข้อมูล)
        """
        print(f"📡 [dim_salesman] กำลังดึงพนักงานสำหรับรหัส: {manager_code}")
        
        # 🔴 ค้นหาเฉพาะ SuperCode อย่างเดียว เพื่อไม่ให้ DAX Error
        dax = f"""
EVALUATE
SELECTCOLUMNS(
    FILTER(
        'dim_salesman', 
        TRIM(UPPER('dim_salesman'[SuperCode])) = "{manager_code.upper()}"
    ),
    "SalesmanCode", 'dim_salesman'[SalesmanCode],
    "SalesmanName", 'dim_salesman'[Salesman_NameThai],
    "SuperCode",    'dim_salesman'[SuperCode]
)
"""
        rows = self._execute_dax(dax, debug=True)
        records = []
        for r in rows:
            emp_id = str(self._get(r,
                "[SalesmanCode]", "dim_salesman[SalesmanCode]",
                default="")).strip()
            if not emp_id:
                continue
            records.append({
                "emp_id":      emp_id,
                "emp_name":    str(self._get(r,
                                   "[SalesmanName]", "dim_salesman[Salesman_NameThai]",
                                   default="")).strip(),
                "super_code":  str(self._get(r,
                                   "[SuperCode]", "dim_salesman[SuperCode]",
                                   default="")).strip(),
            })
        df = pd.DataFrame(records) if records else pd.DataFrame(
            columns=["emp_id", "emp_name", "super_code"])
        print(f"✅ พบพนักงาน {len(df)} คน ใต้ SuperCode {manager_code}")
        return df

    # ── 2. SKU ที่ทีมเคยขาย (ดึงจาก Fabric โดยตรง) ──
    def get_skus_sold_by_team(self, emp_list: list,
                               target_month: int, target_year: int,
                               n_months: int = 6) -> list[str]:
        """
        ดึง SKU ทั้งหมดที่ทีมนี้เคยขายใน n_months เดือนล่าสุด
        ใช้ CALCULATETABLE แทน FILTER('DimDate') ตรงๆ เพื่อให้ relationship ทำงานถูกต้อง
        """
        print(f"📡 [cross_sold] ดึง SKU ที่ทีมเคยขาย ({n_months} เดือนล่าสุด, emp={len(emp_list)} คน)...")
        prev   = self._prev_months(target_month, target_year, n_months)
        date_f = self._dax_date_filter(prev)
        emp_f  = self._emp_treatas(emp_list)

        months_range = f"{prev[-1][0]}/{prev[-1][1]} ถึง {prev[0][0]}/{prev[0][1]}"
        print(f"  ช่วงเวลา: {months_range}")

        dax = f"""
EVALUATE
SUMMARIZECOLUMNS(
    'cross_sold_history_2y_qu'[ProductCode],
    CALCULATETABLE(
        FILTER('DimDate', {date_f})
    ),
    {emp_f}
    "total_qty", SUM('cross_sold_history_2y_qu'[TotalQuantity])
)
"""
        rows = self._execute_dax(dax, debug=True)
        skus = []
        for r in rows:
            sku = str(self._get(r,
                "cross_sold_history_2y_qu[ProductCode]",
                "[ProductCode]", default="")).strip()
            qty = float(self._get(r, "[total_qty]", default=0) or 0)
            if sku and qty > 0:
                skus.append(sku)
        print(f"✅ พบ {len(skus)} SKU ที่ทีมเคยขาย")
        return skus

    # ── 3. ข้อมูลสินค้า (brand, ชื่อ, ราคา) ──────────
    def get_product_info(self, sku_list: list = None) -> pd.DataFrame:
        """ดึงชื่อสินค้า + แบรนด์ + UnitCost จาก dim_product"""
        print("📡 [dim_product] ดึงข้อมูลสินค้า...")
        if sku_list:
            s = ", ".join(f'"{x}"' for x in sku_list)
            table_expr = f"FILTER('dim_product', 'dim_product'[ProductCode] IN {{{s}}})"
        else:
            table_expr = "'dim_product'"

        dax = f"""
EVALUATE
SELECTCOLUMNS(
    {table_expr},
    "ProductCode",       'dim_product'[ProductCode],
    "Brand_NameThai",    'dim_product'[Brand_NameThai],
    "Brand_NameEnglish", 'dim_product'[Brand_NameEnglish],
    "Product_NameThai",  'dim_product'[Product_NameThai],
    "UnitCost",          'dim_product'[UnitCost]
)
"""
        rows = self._execute_dax(dax, debug=True)
        records = []
        for r in rows:
            sku = str(self._get(r,
                "[ProductCode]", "dim_product[ProductCode]",
                default="")).strip()
            if not sku:
                continue
            records.append({
                "sku":                sku,
                "brand_name_thai":    str(self._get(r,
                                          "[Brand_NameThai]", "dim_product[Brand_NameThai]",
                                          default="")).strip(),
                "brand_name_english": str(self._get(r,
                                          "[Brand_NameEnglish]", "dim_product[Brand_NameEnglish]",
                                          default="")).strip(),
                "product_name_thai":  str(self._get(r,
                                          "[Product_NameThai]", "dim_product[Product_NameThai]",
                                          default="")).strip(),
                "unit_cost":          float(self._get(r,
                                          "[UnitCost]", "dim_product[UnitCost]",
                                          default=0) or 0),
            })
        df = pd.DataFrame(records) if records else pd.DataFrame(
            columns=["sku", "brand_name_thai", "brand_name_english",
                     "product_name_thai", "unit_cost"])
        print(f"✅ ดึงข้อมูลสินค้า {len(df)} รายการ")
        return df

    # backward-compat alias
    def get_brands_and_skus(self, sku_list: list = None) -> pd.DataFrame:
        return self.get_product_info(sku_list)

    # ── 4. Historical ย้อนหลัง n เดือน รายคู่ emp×sku ──────────
    def get_historical_sales(self,
                              target_month: int, target_year: int,
                              sku_list: list = None,
                              emp_list: list = None,
                              n_months: int = 3) -> pd.DataFrame:
        """
        ดึงยอดขาย (จำนวนหีบ + บาท) ย้อนหลัง n_months เดือน รายคู่ (emp, sku)
        ใช้ n_months=3 สำหรับ L3M / n_months=6 สำหรับ L6M

        ใช้ CALCULATETABLE สำหรับ date filter แทน FILTER('DimDate',...)
        เพื่อให้ Power BI ใช้ relationship อย่างถูกต้องและเร็วขึ้น
        """
        n_months = max(1, min(int(n_months), 24))
        prev_months = self._prev_months(target_month, target_year, n_months)
        date_filter = self._dax_date_filter(prev_months)
        sku_filter  = self._sku_treatas(sku_list)
        emp_filter  = self._emp_treatas(emp_list)

        months_str = ", ".join(f"{m}/{y}" for m, y in prev_months)
        print(f"📡 [historical] ดึงยอด {n_months} เดือน: {months_str} (emp={len(emp_list) if emp_list else 'all'}, sku={len(sku_list) if sku_list else 'all'})...")

        dax = f"""
EVALUATE
SUMMARIZECOLUMNS(
    'cross_sold_history_2y_qu'[SalesmanCode],
    'cross_sold_history_2y_qu'[ProductCode],
    CALCULATETABLE(
        FILTER('DimDate', {date_filter})
    ),
    {sku_filter}
    {emp_filter}
    "hist_boxes",  SUM('cross_sold_history_2y_qu'[TotalQuantity]),
    "hist_amount", SUM('cross_sold_history_2y_qu'[Amount])
)
"""
        rows = self._execute_dax(dax, debug=True)

        records = []
        for r in rows:
            emp = str(self._get(r,
                "cross_sold_history_2y_qu[SalesmanCode]",
                "[SalesmanCode]", default="")).strip()
            sku = str(self._get(r,
                "cross_sold_history_2y_qu[ProductCode]",
                "[ProductCode]", default="")).strip()
            boxes  = float(self._get(r, "[hist_boxes]",  default=0) or 0)
            amount = float(self._get(r, "[hist_amount]", default=0) or 0)
            if emp and sku:
                records.append({
                    "emp_id":      emp,
                    "sku":         sku,
                    "hist_boxes":  boxes,
                    "hist_amount": amount,
                })

        df = pd.DataFrame(records) if records else pd.DataFrame(
            columns=["emp_id", "sku", "hist_boxes", "hist_amount"])
        df = df[df["hist_boxes"] > 0]
        print(f"✅ historical: {len(df)} รายการ (emp×sku), {df['emp_id'].nunique() if not df.empty else 0} พนักงาน")

        # log ว่าพนักงานไหนไม่มีประวัติ (เพื่อ debug)
        if emp_list and not df.empty:
            found_emps = set(df["emp_id"].unique())
            missing_emps = [e for e in emp_list if e not in found_emps]
            if missing_emps:
                print(f"  ℹ️ ไม่มีประวัติ {n_months}M: {missing_emps} (จะได้ hist_avg=0)")

        return df

    # ── 5. LY Sales รายพนักงาน ────────────────────────
    def get_ly_sales(self,
                     target_month: int, target_year: int,
                     sku_list: list = None,
                     emp_list: list = None) -> pd.DataFrame:
        """
        ดึงยอดขาย (บาท) เดือนเดียวกันปีก่อน รายพนักงาน (ยอดรวมทุกสินค้า)

        ใช้ CALCULATETABLE แทน TREATAS-in-SUMMARIZECOLUMNS
        เพราะ TREATAS กับ dimension column เดียวกันใน SUMMARIZECOLUMNS
        อาจทำให้ได้ผลน้อยกว่าที่คาด (known Power BI behavior)
        """
        ly_year  = target_year - 1
        ly_month = target_month

        print(f"📡 [LY] ดึงยอดขายปีก่อนเดือน {ly_month}/{ly_year} (ยอดรวมทุกสินค้า)...")

        if emp_list:
            emp_str = ", ".join(f'"{e}"' for e in emp_list)
            # ใช้ CALCULATETABLE + VALUES แทน TREATAS ใน SUMMARIZECOLUMNS
            # เพื่อให้แน่ใจว่า filter ทำงานถูกต้องและคืนทุก emp ที่มีข้อมูล
            dax = f"""
EVALUATE
SUMMARIZECOLUMNS(
    'cross_sold_history_2y_qu'[SalesmanCode],
    CALCULATETABLE(
        FILTER('DimDate',
            YEAR('DimDate'[Date]) = {ly_year}
            && MONTH('DimDate'[Date]) = {ly_month}
        )
    ),
    TREATAS({{{emp_str}}}, 'cross_sold_history_2y_qu'[SalesmanCode]),
    "ly_sales", SUM('cross_sold_history_2y_qu'[Amount])
)
"""
        else:
            dax = f"""
EVALUATE
SUMMARIZECOLUMNS(
    'cross_sold_history_2y_qu'[SalesmanCode],
    CALCULATETABLE(
        FILTER('DimDate',
            YEAR('DimDate'[Date]) = {ly_year}
            && MONTH('DimDate'[Date]) = {ly_month}
        )
    ),
    "ly_sales", SUM('cross_sold_history_2y_qu'[Amount])
)
"""
        rows = self._execute_dax(dax, debug=True)

        records = []
        for r in rows:
            emp = str(self._get(r,
                "cross_sold_history_2y_qu[SalesmanCode]",
                "[SalesmanCode]", default="")).strip()
            ly = float(self._get(r, "[ly_sales]", default=0) or 0)
            if emp:
                records.append({"emp_id": emp, "ly_sales": ly})

        df = pd.DataFrame(records) if records else pd.DataFrame(
            columns=["emp_id", "ly_sales"])
        print(f"✅ LY: {len(df)} พนักงาน (จาก {len(emp_list) if emp_list else '?'} ที่ขอ)")

        # ถ้า Fabric ไม่คืน emp ที่ไม่มียอด → เติม 0 ให้ครบทุกคน
        if emp_list:
            df_full = pd.DataFrame({"emp_id": emp_list})
            df = pd.merge(df_full, df, on="emp_id", how="left")
            df["ly_sales"] = df["ly_sales"].fillna(0.0)
            missing = df[df["ly_sales"] == 0]["emp_id"].tolist()
            if missing:
                print(f"  ℹ️ ไม่มียอด LY (ได้ 0): {missing}")

        return df

    # ── 6. Warehouse รายพนักงาน ──────────────────────
    def get_warehouse_by_emp(self, emp_list: list) -> pd.DataFrame:
        """ดึง WarehouseCode หลักของแต่ละพนักงาน"""
        if not emp_list:
            return pd.DataFrame(columns=["emp_id", "warehouse_code"])

        emp_f = self._emp_treatas(emp_list)
        dax = f"""
EVALUATE
SUMMARIZECOLUMNS(
    'cross_sold_history_2y_qu'[SalesmanCode],
    'cross_sold_history_2y_qu'[WarehouseCode],
    {emp_f}
    "cnt", COUNTROWS('cross_sold_history_2y_qu')
)
ORDER BY 'cross_sold_history_2y_qu'[SalesmanCode], [cnt] DESC
"""
        rows = self._execute_dax(dax)
        records = []
        for r in rows:
            emp = str(self._get(r,
                "cross_sold_history_2y_qu[SalesmanCode]",
                "[SalesmanCode]", default="")).strip()
            wh = str(self._get(r,
                "cross_sold_history_2y_qu[WarehouseCode]",
                "[WarehouseCode]", default="")).strip()
            if emp:
                records.append({"emp_id": emp, "warehouse_code": wh})

        df = pd.DataFrame(records) if records else pd.DataFrame(
            columns=["emp_id", "warehouse_code"])
        return df.drop_duplicates(subset="emp_id", keep="first")