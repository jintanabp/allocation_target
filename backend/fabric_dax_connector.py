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

# โหลด .env เมื่อ import โมดูลโดยตรง (เช่นเทส) — main.py ก็โหลดก่อน import อยู่แล้ว
from .load_env import load_project_dotenv

load_project_dotenv()


class FabricDAXConnector:
    def __init__(self):
        # ค่า default ใช้สำหรับ dev — production ตั้งใน .env หรือ environment
        self.client_id  = os.environ.get("FABRIC_CLIENT_ID", "d0d1f812-d677-490e-a9df-25c00baea1ab")
        self.tenant_id  = os.environ.get("FABRIC_TENANT_ID", "e442d6a7-a8dc-4ac8-880b-d272b11642e9")
        # Semantic model ใหม่ (มี Dim_Product / Dim_Salesman / tga_target_salesman_next)
        self.dataset_id = os.environ.get("FABRIC_DATASET_ID", "dcff7153-5257-45ea-84f5-9b9b6387920b")
        # ถ้าใส่จะยิง executeQueries แบบ group/workspace — บางทีสะดวกตอนเช็คสิทธิ์ใน workspace
        self.workspace_id = (os.environ.get("FABRIC_WORKSPACE_ID") or "").strip()
        self.authority  = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.scope      = ["https://analysis.windows.net/powerbi/api/.default"]

        os.makedirs("data", exist_ok=True)
        self.cache_file = "data/token_cache.bin"
        self.cache: msal.SerializableTokenCache | None = None
        self._pca: msal.PublicClientApplication | None = None
        self._cca: msal.ConfidentialClientApplication | None = None

        secret = (os.environ.get("FABRIC_CLIENT_SECRET") or "").strip()
        self._use_service_principal = bool(secret)

        if self._use_service_principal:
            self._cca = msal.ConfidentialClientApplication(
                self.client_id,
                client_credential=secret,
                authority=self.authority,
            )
            print(
                "📡 Fabric auth: Service Principal (FABRIC_CLIENT_SECRET) — "
                "ไม่เปิดเบราว์เซอร์ล็อกอิน"
            )
            cid = self.client_id[:8] + "…" if len(self.client_id) > 8 else self.client_id
            if self.workspace_id:
                print(
                    f"📡 DAX REST: …/groups/{self.workspace_id}/datasets/"
                    f"{self.dataset_id}/executeQueries  (FABRIC_CLIENT_ID={cid})"
                )
            else:
                print(
                    f"📡 DAX REST: …/myorg/datasets/{self.dataset_id}/executeQueries  "
                    f"(FABRIC_CLIENT_ID={cid}; แนะนำใส่ FABRIC_WORKSPACE_ID)"
                )
        else:
            self.cache = msal.SerializableTokenCache()
            if os.path.exists(self.cache_file):
                with open(self.cache_file, "r") as f:
                    self.cache.deserialize(f.read())
            atexit.register(self._save_cache)
            self._pca = msal.PublicClientApplication(
                self.client_id,
                authority=self.authority,
                token_cache=self.cache,
            )
            print("📡 Fabric auth: ล็อกอินผู้ใช้ (interactive / token cache)")

    # ──────────────────────────────────────────────
    # Auth / cache
    # ──────────────────────────────────────────────
    def _save_cache(self) -> None:
        if self._use_service_principal or self.cache is None:
            return
        if self.cache.has_state_changed:
            with open(self.cache_file, "w") as f:
                f.write(self.cache.serialize())

    def _get_access_token(self) -> str:
        if self._cca is not None:
            result = self._cca.acquire_token_for_client(scopes=self.scope)
            if "access_token" in result:
                return result["access_token"]
            err = result.get("error_description") or result.get("error") or str(result)
            raise Exception(f"❌ Service Principal token ไม่สำเร็จ: {err}")

        assert self._pca is not None
        accounts = self._pca.get_accounts()
        if accounts:
            result = self._pca.acquire_token_silent(self.scope, account=accounts[0])
        else:
            print("⏳ ไม่พบ token กำลังเปิดเบราว์เซอร์ให้ Login...")
            result = self._pca.acquire_token_interactive(scopes=self.scope)

        if "access_token" in result:
            self._save_cache()
            return result["access_token"]
        raise Exception(f"❌ Login ไม่สำเร็จ: {result.get('error_description')}")

    def diagnose_powerbi_rest_access(self) -> None:
        """
        เรียก Power BI REST แบบ GET (ไม่รัน DAX) เพื่อแยกว่า 404 มาจาก
        \"ไม่เห็น workspace/dataset\" หรือเฉพาะ executeQueries
        รันจาก project root: python scripts/test_powerbi_access.py
        """
        token = self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        base = "https://api.powerbi.com/v1.0/myorg"

        print("── Power BI REST diagnose (Service Principal) ──")
        if self.workspace_id:
            url_list = f"{base}/groups/{self.workspace_id}/datasets"
            r = requests.get(url_list, headers=headers, timeout=60)
            print(f"GET .../groups/<workspace>/datasets → HTTP {r.status_code}")
            if r.status_code == 200:
                ids = [d.get("id") for d in r.json().get("value", [])]
                print(f"   dataset ใน workspace นี้: {len(ids)} รายการ")
                for i in ids[:30]:
                    mark = " ← FABRIC_DATASET_ID" if i == self.dataset_id else ""
                    print(f"   · {i}{mark}")
                if self.dataset_id not in ids:
                    print(
                        "   ⚠️  FABRIC_DATASET_ID ไม่อยู่ในรายการนี้ "
                        "(ID ผิด workspace หรือชื่อ dataset ไม่ตรง)"
                    )
            else:
                print(r.text[:800])

            url_ds = (
                f"{base}/groups/{self.workspace_id}/datasets/{self.dataset_id}"
            )
            r2 = requests.get(url_ds, headers=headers, timeout=60)
            print(f"GET .../groups/<workspace>/datasets/<datasetId> → HTTP {r2.status_code}")
            if r2.status_code == 200:
                self._print_dataset_mode_hints(r2.json())
            else:
                print(r2.text[:800])
        else:
            print("(ไม่มี FABRIC_WORKSPACE_ID — ข้ามการ list ใน group)")

        r3 = requests.get(f"{base}/datasets/{self.dataset_id}", headers=headers, timeout=60)
        print(f"GET .../myorg/datasets/<datasetId> → HTTP {r3.status_code}")
        if r3.status_code == 200:
            self._print_dataset_mode_hints(r3.json())
        else:
            print(r3.text[:800])
        print("── จบ diagnose ──")

    @staticmethod
    def _print_dataset_mode_hints(meta: dict) -> None:
        """ช่วยอธิบายว่าทำไม executeQueries อาจ 404 ทั้งที่ GET สำเร็จ"""
        cpt = meta.get("contentProviderType")
        up = meta.get("upstreamDatasets") or []
        print(f"   contentProviderType: {cpt}")
        print(f"   upstreamDatasets: {len(up)} รายการ" + (f" {up}" if up else ""))
        if up or (cpt and "Composite" in str(cpt)):
            print(
                "   ⚠️  โมเดลแบบ composite / มี upstream semantic model — "
                "Power BI REST executeQueries มักไม่รองรับ (ได้ 404) แม้มีสิทธิ์; "
                "ลองชี้ FABRIC_DATASET_ID ไปที่ upstream ที่เป็น import หรือใช้ XMLA"
            )

    # ──────────────────────────────────────────────
    # Core DAX executor
    # ──────────────────────────────────────────────
    def _execute_dax(self, dax_query: str, debug: bool = False) -> list[dict]:
        """
        ส่ง DAX query ไปยัง Power BI REST API แล้วคืน list of row dicts
        debug=True → print first row เพื่อดู key format จริงๆ
        """
        token = self._get_access_token()
        base = "https://api.powerbi.com/v1.0/myorg"
        group_url = (
            f"{base}/groups/{self.workspace_id}/datasets/"
            f"{self.dataset_id}/executeQueries"
            if self.workspace_id
            else None
        )
        myorg_url = f"{base}/datasets/{self.dataset_id}/executeQueries"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "queries": [{"query": dax_query}],
            "serializerSettings": {"includeNulls": False},
        }

        response = requests.post(
            group_url or myorg_url, headers=headers, json=payload, timeout=60
        )
        if (
            response.status_code == 404
            and group_url
            and "PowerBIEntityNotFound" in response.text
        ):
            print("📡 executeQueries 404 แบบ group — ลองสำรองแบบ myorg/datasets/…")
            response = requests.post(
                myorg_url, headers=headers, json=payload, timeout=60
            )

        if response.status_code != 200:
            msg = (
                f"❌ DAX query failed (HTTP {response.status_code}): "
                f"{response.text[:600]}"
            )
            if response.status_code == 404 and "PowerBIEntityNotFound" in response.text:
                msg += (
                    " | ถ้า GET dataset สำเร็จแต่ executeQueries 404: อาจเป็นโมเดล composite/"
                    "มี upstream semantic model — REST ไม่รองรับ (รัน "
                    "python scripts/test_powerbi_access.py เพื่อดู contentProviderType)"
                )
            raise Exception(msg)

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
        """ดึงรายการ SuperCode ที่ไม่ซ้ำกันทั้งหมดจาก Dim_Salesman"""
        print("📡 [Dim_Salesman] กำลังดึงรายชื่อ SuperCode ทั้งหมด...")
        dax = """
EVALUATE
SUMMARIZECOLUMNS(
    'Dim_Salesman'[SuperCode]
)
"""
        rows = self._execute_dax(dax)
        codes = set()
        for r in rows:
            sc = str(self._get(r, "[SuperCode]", "Dim_Salesman[SuperCode]", default="")).strip()
            # กรองค่าว่าง หรือค่าที่ไม่ถูกต้องทิ้ง
            if sc and sc.upper() != "NONE" and sc != "0":
                codes.add(sc.upper())
                
        sorted_codes = sorted(list(codes))
        print(f"✅ พบ SuperCode ทั้งหมด {len(sorted_codes)} รหัส")
        return sorted_codes

    def get_trf_select_supervisor_rows(self) -> list[dict]:
        """
        ดึงจากตาราง trf_select_supervisor (ใช้หน้า login):
          - SUPERVISORCODE: รหัส Supervisor
          - DEPENDON: รหัส Manager ที่ supervisor ขึ้นต่อ (manager ดูแลหลาย supervisor ได้)
        """
        print("📡 [trf_select_supervisor] กำลังดึง SUPERVISORCODE / DEPENDON...")
        dax = """
EVALUATE
SELECTCOLUMNS(
    'trf_select_supervisor',
    "SUPERVISORCODE", 'trf_select_supervisor'[SUPERVISORCODE],
    "DEPENDON", 'trf_select_supervisor'[DEPENDON]
)
"""
        try:
            rows = self._execute_dax(dax)
        except Exception as ex:
            print(f"⚠️ trf_select_supervisor DAX ล้มเหลว: {ex}")
            return []

        out: list[dict] = []
        for r in rows or []:
            sup = str(
                self._get(r, "[SUPERVISORCODE]", "trf_select_supervisor[SUPERVISORCODE]", default="")
            ).strip()
            dep = str(
                self._get(r, "[DEPENDON]", "trf_select_supervisor[DEPENDON]", default="")
            ).strip()
            if not sup or sup.upper() in ("NONE", "0", "(BLANK)"):
                continue
            out.append({"supervisor_code": sup.upper(), "depend_on": dep.upper() if dep else ""})

        print(f"✅ trf_select_supervisor: {len(out)} แถว")
        return out

    def get_acc_user_control_rows(self) -> list[dict]:
        """
        ACC_USER_CONTROL: คอลัมน์ EMAIL และ USERPL (สิทธิ์เข้าใช้เป็นรหัส Supervisor หรือ Manager)
        แถวซ้ำ EMAIL+USERPL — ฝั่ง access_control จะรวมเป็น set
        """
        print("📡 [ACC_USER_CONTROL] EMAIL / USERPL...")
        dax = """
EVALUATE
SELECTCOLUMNS(
    'ACC_USER_CONTROL',
    "EMAIL", 'ACC_USER_CONTROL'[EMAIL],
    "USERPL", 'ACC_USER_CONTROL'[USERPL]
)
"""
        try:
            rows = self._execute_dax(dax)
        except Exception as ex:
            print(f"⚠️ ACC_USER_CONTROL DAX ล้มเหลว: {ex}")
            return []

        out: list[dict] = []
        for r in rows or []:
            em = str(
                self._get(r, "[EMAIL]", "ACC_USER_CONTROL[EMAIL]", default="")
            ).strip()
            upl = str(
                self._get(r, "[USERPL]", "ACC_USER_CONTROL[USERPL]", default="")
            ).strip()
            if not em or not upl:
                continue
            out.append({"email": em, "userpl": upl.upper()})
        print(f"✅ ACC_USER_CONTROL: {len(out)} แถว (dedupe ฝั่ง access control)")
        return out

    # ── 1. พนักงานจาก SuperCode ─────────────────────────────────────
    def get_employees_by_manager(self, manager_code: str) -> pd.DataFrame:
        """
        ดึงรายชื่อพนักงานจาก Dim_Salesman โดย SuperCode 
        (เพิ่ม TRIM และ UPPER เพื่อป้องกันปัญหาเคาะวรรคซ่อนอยู่ในฐานข้อมูล)
        """
        print(f"📡 [Dim_Salesman] กำลังดึงพนักงานสำหรับรหัส: {manager_code}")
        
        # 🔴 ค้นหาเฉพาะ SuperCode อย่างเดียว เพื่อไม่ให้ DAX Error
        dax = f"""
EVALUATE
SELECTCOLUMNS(
    FILTER(
        'Dim_Salesman', 
        TRIM(UPPER('Dim_Salesman'[SuperCode])) = "{manager_code.upper()}"
    ),
    "SalesmanCode", 'Dim_Salesman'[SalesmanCode],
    "SalesmanName", 'Dim_Salesman'[Salesman_NameThai],
    "SuperCode",    'Dim_Salesman'[SuperCode]
)
"""
        rows = self._execute_dax(dax, debug=True)
        records = []
        for r in rows:
            emp_id = str(self._get(r,
                "[SalesmanCode]", "Dim_Salesman[SalesmanCode]",
                default="")).strip()
            if not emp_id:
                continue
            records.append({
                "emp_id":      emp_id,
                "emp_name":    str(self._get(r,
                                   "[SalesmanName]", "Dim_Salesman[Salesman_NameThai]",
                                   default="")).strip(),
                "super_code":  str(self._get(r,
                                   "[SuperCode]", "Dim_Salesman[SuperCode]",
                                   default="")).strip(),
            })
        df = pd.DataFrame(records) if records else pd.DataFrame(
            columns=["emp_id", "emp_name", "super_code"])
        print(f"✅ พบพนักงาน {len(df)} คน ใต้ SuperCode {manager_code}")
        return df

    # ── 1.1 ชื่อ Supervisor จากรหัส SuperCode ─────────────────────────
    def get_supervisor_name(self, super_code: str) -> str:
        """
        ดึงชื่อ Supervisor สำหรับแสดงบนหน้า
        1) ดึงจาก Dim_Super[Namethai] โดย match Dim_Super[Code]
        2) fallback: Dim_Salesman (SalesmanCode == super_code) กรณี supervisor ก็เป็นพนักงานขายใน dim
        ถ้าไม่เจอจะคืน "" (ให้ frontend แสดงแค่ code)
        """
        sc = str(super_code or "").strip()
        if not sc:
            return ""

        # (1) Dim_Super (clean: fix table/columns)
        dax_super = f"""
EVALUATE
SELECTCOLUMNS(
    FILTER(
        'Dim_Super',
        TRIM(UPPER('Dim_Super'[Code])) = "{sc.upper()}"
    ),
    "SuperNameThai", 'Dim_Super'[Namethai]
)
"""
        try:
            rows = self._execute_dax(dax_super, debug=False)
            for r in rows or []:
                name = str(self._get(r, "[SuperNameThai]", "Dim_Super[Namethai]", default="")).strip()
                if name:
                    return name
        except Exception:
            pass

        # (2) fallback Dim_Salesman
        dax_salesman = f"""
EVALUATE
SELECTCOLUMNS(
    FILTER(
        'Dim_Salesman',
        TRIM(UPPER('Dim_Salesman'[SalesmanCode])) = "{sc.upper()}"
    ),
    "SalesmanName", 'Dim_Salesman'[Salesman_NameThai]
)
"""
        try:
            rows = self._execute_dax(dax_salesman, debug=False)
            for r in rows or []:
                name = str(self._get(r, "[SalesmanName]", "Dim_Salesman[Salesman_NameThai]", default="")).strip()
                if name:
                    return name
        except Exception:
            pass

        return ""

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
        """ดึงข้อมูลสินค้า + แบรนด์จาก Dim_Product"""
        print("📡 [Dim_Product] ดึงข้อมูลสินค้า...")
        if sku_list:
            s = ", ".join(f'"{x}"' for x in sku_list)
            table_expr = f"FILTER('Dim_Product', 'Dim_Product'[ProductCode] IN {{{s}}})"
        else:
            table_expr = "'Dim_Product'"

        dax = f"""
EVALUATE
SELECTCOLUMNS(
    {table_expr},
    "ProductCode",         'Dim_Product'[ProductCode],
    "Brand",               'Dim_Product'[Brand],
    "Brand_NameThai",      'Dim_Product'[Brand_NameThai],
    "Product_NameThai",    'Dim_Product'[Product_NameThai],
    "Product_NameEnglish", 'Dim_Product'[Product_NameEnglish],
    "UnitCost",            COALESCE(
                             RELATED('cfm_produc_master'[ACTUALCOSTPERUNIT]),
                             LOOKUPVALUE(
                               'cfm_produc_master'[ACTUALCOSTPERUNIT],
                               'cfm_produc_master'[PRODUCTCODE],
                               'Dim_Product'[ProductCode]
                             )
                           ),
    "CostPerUnit",         COALESCE(
                             RELATED('cfm_produc_master'[COSTPERUNIT]),
                             LOOKUPVALUE(
                               'cfm_produc_master'[COSTPERUNIT],
                               'cfm_produc_master'[PRODUCTCODE],
                               'Dim_Product'[ProductCode]
                             )
                           ),
    "CreditUnitPrice",     VAR pc = 'Dim_Product'[ProductCode]
                           VAR t = TODAY()
                           VAR cr =
                             CALCULATE(
                               MAX('cfm_product_characteristic'[CREDITUNITPRICE]),
                               FILTER(
                                 ALL('cfm_product_characteristic'),
                                 TRIM(FORMAT('cfm_product_characteristic'[PRODUCTCODE], "0"))
                                   = TRIM(FORMAT(pc, "0"))
                                   && IFERROR(
                                     VALUE(TRIM(FORMAT('cfm_product_characteristic'[PRODUCTSIZE], "0"))),
                                     -1
                                   ) = 0
                                   && 'cfm_product_characteristic'[FROMDATE] <= t
                                   && 'cfm_product_characteristic'[TODATE] >= t
                               )
                             )
                           RETURN COALESCE(cr, 0)
)
"""
        rows = self._execute_dax(dax, debug=True)
        records = []
        for r in rows:
            sku = str(self._get(r,
                "[ProductCode]", "Dim_Product[ProductCode]",
                default="")).strip()
            if not sku:
                continue
            records.append({
                "sku":                sku,
                "brand":              str(self._get(r, "[Brand]", "Dim_Product[Brand]", default="")).strip(),
                "brand_name_thai":    str(self._get(r,
                                          "[Brand_NameThai]", "Dim_Product[Brand_NameThai]",
                                          default="")).strip(),
                # semantic model ใหม่นี้ไม่มี Brand_NameEnglish ตาม requirement → ใส่ไว้เพื่อ backward-compat
                "brand_name_english": "",
                "product_name_thai":  str(self._get(r,
                                          "[Product_NameThai]", "Dim_Product[Product_NameThai]",
                                          default="")).strip(),
                "product_name_english": str(self._get(r,
                                          "[Product_NameEnglish]", "Dim_Product[Product_NameEnglish]",
                                          default="")).strip(),
                # unit cost จาก cfm (ข้อมูลอ้างอิง — ราคา/หีบหลักใช้จากยอดขายเดือนล่าสุด)
                "unit_cost":          float(self._get(r,
                                          "[UnitCost]", "cfm_produc_master[ACTUALCOSTPERUNIT]",
                                          default=0) or 0),
                "cost_per_unit":      float(self._get(r,
                                          "[CostPerUnit]", "cfm_produc_master[COSTPERUNIT]",
                                          default=0) or 0),
                "credit_unit_price":  float(self._get(r,
                                          "[CreditUnitPrice]",
                                          "cfm_product_characteristic[CREDITUNITPRICE]",
                                          default=0) or 0),
            })
        df = pd.DataFrame(records) if records else pd.DataFrame(
            columns=["sku", "brand", "brand_name_thai", "brand_name_english",
                     "product_name_thai", "product_name_english", "unit_cost", "cost_per_unit",
                     "credit_unit_price"])
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

    def get_calendar_year_sales_by_emp_sku(
        self,
        calendar_year: int,
        sku_list: list | None = None,
        emp_list: list | None = None,
    ) -> pd.DataFrame:
        """
        ยอดขาย (จำนวนหีบ + บาท) รายคู่ (emp, sku) รวมทุกเดือนในปีปฏิทินที่ระบุ (Jan–Dec)
        ใช้ตรวจว่า SKU ไม่มียอดทั้งปีปัจจุบันและปีก่อน (สินค้าใหม่) ตอน optimize
        """
        cy = int(calendar_year)
        sku_filter = self._sku_treatas(sku_list)
        emp_filter = self._emp_treatas(emp_list)

        print(
            f"📡 [historical CY] ปีปฏิทิน {cy} ราย emp×sku "
            f"(emp={len(emp_list) if emp_list else 'all'}, sku={len(sku_list) if sku_list else 'all'})..."
        )

        dax = f"""
EVALUATE
SUMMARIZECOLUMNS(
    'cross_sold_history_2y_qu'[SalesmanCode],
    'cross_sold_history_2y_qu'[ProductCode],
    CALCULATETABLE(
        FILTER('DimDate',
            YEAR('DimDate'[Date]) = {cy}
        )
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
            boxes = float(self._get(r, "[hist_boxes]", default=0) or 0)
            amount = float(self._get(r, "[hist_amount]", default=0) or 0)
            if emp and sku:
                records.append({
                    "emp_id": emp,
                    "sku": sku,
                    "hist_boxes": boxes,
                    "hist_amount": amount,
                })

        df = pd.DataFrame(records) if records else pd.DataFrame(
            columns=["emp_id", "sku", "hist_boxes", "hist_amount"])
        if not df.empty:
            df = df[df["hist_boxes"] > 0]
        print(
            f"✅ historical CY {cy}: {len(df)} รายการ (emp×sku), "
            f"{df['emp_id'].nunique() if not df.empty else 0} พนักงาน"
        )
        return df

    def get_same_month_prior_year_by_emp_sku(
        self,
        target_month: int,
        target_year: int,
        sku_list: list | None = None,
        emp_list: list | None = None,
    ) -> pd.DataFrame:
        """
        ยอดขายจริง (จำนวนหีบ + บาท) รายคู่ (emp, sku) เฉพาะเดือนเดียวกับงวดที่เลือก แต่ปีที่แล้ว (YoY)
        ใช้ประกอบการเกลี่ยหีบร่วมกับช่วง 3M/6M เพื่อให้สัดส่วนใกล้ฤดูกาลเดียวกันของปีก่อน
        """
        ly_year = int(target_year) - 1
        ly_month = int(target_month)
        sku_filter = self._sku_treatas(sku_list)
        emp_filter = self._emp_treatas(emp_list)

        print(
            f"📡 [historical YoY] เดือน {ly_month}/{ly_year} ราย emp×sku "
            f"(emp={len(emp_list) if emp_list else 'all'}, sku={len(sku_list) if sku_list else 'all'})..."
        )

        dax = f"""
EVALUATE
SUMMARIZECOLUMNS(
    'cross_sold_history_2y_qu'[SalesmanCode],
    'cross_sold_history_2y_qu'[ProductCode],
    CALCULATETABLE(
        FILTER('DimDate',
            YEAR('DimDate'[Date]) = {ly_year}
            && MONTH('DimDate'[Date]) = {ly_month}
        )
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
            emp = str(
                self._get(
                    r,
                    "cross_sold_history_2y_qu[SalesmanCode]",
                    "[SalesmanCode]",
                    default="",
                )
            ).strip()
            sku = str(
                self._get(
                    r,
                    "cross_sold_history_2y_qu[ProductCode]",
                    "[ProductCode]",
                    default="",
                )
            ).strip()
            boxes = float(self._get(r, "[hist_boxes]", default=0) or 0)
            amount = float(self._get(r, "[hist_amount]", default=0) or 0)
            if emp and sku:
                records.append(
                    {
                        "emp_id": emp,
                        "sku": sku,
                        "hist_boxes": boxes,
                        "hist_amount": amount,
                    }
                )

        df = pd.DataFrame(records) if records else pd.DataFrame(
            columns=["emp_id", "sku", "hist_boxes", "hist_amount"]
        )
        if not df.empty:
            df = df[df["hist_boxes"] > 0]
        print(
            f"✅ historical YoY: {len(df)} รายการ (emp×sku), "
            f"{df['emp_id'].nunique() if not df.empty else 0} พนักงาน"
        )
        return df

    def get_prev_month_by_emp_sku(
        self,
        target_month: int,
        target_year: int,
        sku_list: list | None = None,
        emp_list: list | None = None,
    ) -> pd.DataFrame:
        """
        ยอดขายจริง (จำนวนหีบ + บาท) รายคู่ (emp, sku) เฉพาะ "เดือนที่แล้ว" (เดือนก่อนงวดที่เลือก)
        ใช้แสดงในผลลัพธ์เพื่อให้เห็น momentum ล่าสุด
        """
        prev = self._prev_months(int(target_month), int(target_year), n=1)
        if not prev:
            return pd.DataFrame(columns=["emp_id", "sku", "hist_boxes", "hist_amount"])
        m, y = prev[0]
        sku_filter = self._sku_treatas(sku_list)
        emp_filter = self._emp_treatas(emp_list)

        print(
            f"📡 [historical prev] เดือน {m}/{y} ราย emp×sku "
            f"(emp={len(emp_list) if emp_list else 'all'}, sku={len(sku_list) if sku_list else 'all'})..."
        )

        dax = f"""
EVALUATE
SUMMARIZECOLUMNS(
    'cross_sold_history_2y_qu'[SalesmanCode],
    'cross_sold_history_2y_qu'[ProductCode],
    CALCULATETABLE(
        FILTER('DimDate',
            YEAR('DimDate'[Date]) = {y}
            && MONTH('DimDate'[Date]) = {m}
        )
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
            emp = str(
                self._get(
                    r,
                    "cross_sold_history_2y_qu[SalesmanCode]",
                    "[SalesmanCode]",
                    default="",
                )
            ).strip()
            sku = str(
                self._get(
                    r,
                    "cross_sold_history_2y_qu[ProductCode]",
                    "[ProductCode]",
                    default="",
                )
            ).strip()
            boxes = float(self._get(r, "[hist_boxes]", default=0) or 0)
            amount = float(self._get(r, "[hist_amount]", default=0) or 0)
            if emp and sku:
                records.append(
                    {
                        "emp_id": emp,
                        "sku": sku,
                        "hist_boxes": boxes,
                        "hist_amount": amount,
                    }
                )

        df = pd.DataFrame(records) if records else pd.DataFrame(
            columns=["emp_id", "sku", "hist_boxes", "hist_amount"]
        )
        if not df.empty:
            df = df[df["hist_boxes"] > 0]
        print(
            f"✅ historical prev: {len(df)} รายการ (emp×sku), "
            f"{df['emp_id'].nunique() if not df.empty else 0} พนักงาน"
        )
        return df

    # ── Price per box จากยอดขายเดือนล่าสุด (Amount / TotalQuantity) ──────────
    def get_latest_price_per_box_by_sku(
        self,
        target_month: int,
        target_year: int,
        sku_list: list[str],
    ) -> pd.DataFrame:
        """
        คืนราคา/หีบราย SKU จากเดือนล่าสุด (เดือนก่อนงวดที่เลือก):
          price_per_box = SUM(Amount) / SUM(TotalQuantity)

        ใช้ cross_sold_history_2y_qu + DimDate
        ถ้า SKU ไม่มีข้อมูลเดือนนั้น จะไม่ถูกส่งกลับ (ให้ caller ถือว่า missing)
        """
        if not sku_list:
            return pd.DataFrame(columns=["sku", "price_per_box"])

        prev = self._prev_months(target_month, target_year, n=1)
        date_filter = self._dax_date_filter(prev)
        sku_filter = self._sku_treatas(sku_list)
        m, y = prev[0]
        print(f"📡 [price] ราคา/หีบจากยอดขายเดือน {m}/{y} (Amount÷Qty, sku={len(sku_list)})...")

        dax = f"""
EVALUATE
SUMMARIZECOLUMNS(
    'cross_sold_history_2y_qu'[ProductCode],
    CALCULATETABLE(
        FILTER('DimDate', {date_filter})
    ),
    {sku_filter}
    "qty", SUM('cross_sold_history_2y_qu'[TotalQuantity]),
    "amt", SUM('cross_sold_history_2y_qu'[Amount])
)
"""
        rows = self._execute_dax(dax, debug=True)
        records = []
        for r in rows:
            sku = str(
                self._get(
                    r,
                    "cross_sold_history_2y_qu[ProductCode]",
                    "[ProductCode]",
                    default="",
                )
            ).strip()
            qty = float(self._get(r, "[qty]", default=0) or 0)
            amt = float(self._get(r, "[amt]", default=0) or 0)
            if not sku or qty <= 0:
                continue
            records.append({"sku": sku, "price_per_box": amt / qty})
        df = pd.DataFrame(records) if records else pd.DataFrame(columns=["sku", "price_per_box"])
        if not df.empty:
            df["sku"] = df["sku"].astype(str).str.strip()
            df["price_per_box"] = pd.to_numeric(df["price_per_box"], errors="coerce").fillna(0.0)
        print(f"✅ price: {len(df)} SKU")
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

    def get_tga_max_effective_raw(self):
        """
        ค่า MAX(EFFECTIVEDATE) จาก tga_target_salesman_next (ทุกแถวมักเป็นค่าเดียวกัน)
        ใช้ตรวจว่างวดเป้าที่ผู้ใช้เลือกยังตรงกับ snapshot ปัจจุบันหรือไม่
        """
        t = os.environ.get("TGA_TABLE_NAME", "tga_target_salesman_next").strip()
        c_eff = os.environ.get("TGA_COL_EFFECTIVE", "EFFECTIVEDATE").strip()
        print(f"📡 [{t}] ดึง MAX({c_eff}) เพื่อตรวจงวดเป้า (TGA snapshot)...")
        dax = f"""
EVALUATE
ROW("MaxEffective", MAX('{t}'[{c_eff}]))
"""
        rows = self._execute_dax(dax, debug=True)
        if not rows:
            return None
        r = rows[0]
        return self._get(r, "[MaxEffective]", "MaxEffective", default=None)

    # ── 7. เป้าหมายจาก tga_target_salesman_next (semantic model) ─────────────
    def get_tga_target_salesman(
        self,
        emp_list: list,
        target_month: int,
        target_year: int,
    ) -> pd.DataFrame:
        """
        ดึงเป้าหีบรายคู่พนักงาน×สินค้าจาก tga_target_salesman_next (semantic model เดียวกับยอดขายย้อนหลัง)

        ใช้เฉพาะ: SALESMANCODE, PRODUCTCODE, QUANTITYCASE (เป้าหีบ) — นำไปคูณราคาต่อหีบจาก dim_product ใน main
        ตารางเป้าถูกเคลียร์รายเดือนฝั่งข้อมูล → ค่าเริ่มต้น **ไม่** กรองตาม EFFECTIVEDATE

        ไม่กรองด้วยรายการ SKU จากประวัติขาย (L6M)
        env: TGA_TABLE_NAME, TGA_COL_SALESMAN, TGA_COL_PRODUCT, TGA_COL_QUANTITY,
        TGA_FILTER_BY_EFFECTIVE=1 เท่านั้นจึงกรอง YEAR/MONTH ที่ TGA_COL_EFFECTIVE (ค่าเริ่มต้น EFFECTIVEDATE)
        """
        t = os.environ.get("TGA_TABLE_NAME", "tga_target_salesman_next").strip()
        c_emp = os.environ.get("TGA_COL_SALESMAN", "SALESMANCODE").strip()
        c_prod = os.environ.get("TGA_COL_PRODUCT", "PRODUCTCODE").strip()
        c_qty = os.environ.get("TGA_COL_QUANTITY", "QUANTITYCASE").strip()
        c_eff = os.environ.get("TGA_COL_EFFECTIVE", "EFFECTIVEDATE").strip()
        filter_period = os.environ.get("TGA_FILTER_BY_EFFECTIVE", "0").strip().lower() in (
            "1", "true", "yes", "y",
        )

        if not emp_list:
            return pd.DataFrame(columns=["emp_id", "sku", "qty"])

        emp_str = ", ".join(f'"{str(e)}"' for e in emp_list)

        eff_filters = ""
        if filter_period and c_eff:
            y_ce = int(target_year)
            tm = int(target_month)
            eff_filters = (
                f", YEAR('{t}'[{c_eff}]) = {y_ce}, "
                f"MONTH('{t}'[{c_eff}]) = {tm}"
            )

        _tga_log_suffix = (
            f", กรองงวดจาก {c_eff}"
            if filter_period
            else " — ไม่กรองวันที่ (ตารางเป็น snapshot เดือนปัจจุบัน)"
        )
        print(
            f"📡 [{t}] ดึงเป้า TGA (emp={len(emp_list)}, "
            f"QUANTITYCASE ต่อ SALESMANCODE×PRODUCTCODE{_tga_log_suffix})..."
        )

        dax = f"""
EVALUATE
CALCULATETABLE(
    SUMMARIZECOLUMNS(
        '{t}'[{c_emp}],
        '{t}'[{c_prod}],
        "target_qty", SUM('{t}'[{c_qty}])
    ),
    TREATAS({{{emp_str}}}, '{t}'[{c_emp}]){eff_filters}
)
"""
        rows = self._execute_dax(dax, debug=True)
        records = []
        for r in rows:
            emp = str(
                self._get(
                    r,
                    f"{t}[{c_emp}]",
                    f"[{c_emp}]",
                    "tga_target_salesman_next[SALESMANCODE]",
                    "[SALESMANCODE]",
                    default="",
                )
            ).strip()
            sku = str(
                self._get(
                    r,
                    f"{t}[{c_prod}]",
                    f"[{c_prod}]",
                    "tga_target_salesman_next[PRODUCTCODE]",
                    "[PRODUCTCODE]",
                    default="",
                )
            ).strip()
            qty = float(self._get(r, "[target_qty]", default=0) or 0)
            if emp and sku and qty != 0:
                records.append({"emp_id": emp, "sku": sku, "qty": qty})

        df = pd.DataFrame(records) if records else pd.DataFrame(
            columns=["emp_id", "sku", "qty"]
        )
        print(f"✅ TGA targets: {len(df)} แถว (emp×sku)")
        return df