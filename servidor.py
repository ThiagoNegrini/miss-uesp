#!/usr/bin/env python3
"""Central de Controle UESP 2027 — servidor local com controle de chegada."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
import sqlite3, json, socket, io
from pathlib import Path
from email.parser import BytesParser
from email.policy import default
from datetime import datetime

HERE=Path(__file__).resolve().parent
AUDITION_DB=HERE/"banco_audicao.db"
CONTEST_DB=HERE/"banco_concurso.db"
CATEGORIES=["Rainha do Samba","Passista de Ouro","Diva do Samba (Plus Size)"]

def dbconn(database=AUDITION_DB):
    """Abre o banco solicitado sem criar tabelas da Audição no banco do Concurso."""
    target=Path(database).resolve()
    con=sqlite3.connect(target,timeout=15)
    con.row_factory=sqlite3.Row
    if target == AUDITION_DB.resolve():
        con.execute("""CREATE TABLE IF NOT EXISTS candidates (
          id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, name TEXT NOT NULL,
          imported_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(category,name))""")
        # Campos operacionais usados pela tela de chegada e pelas fotos/relatórios.
        # ALTER TABLE é aditivo: preserva os candidatos e os dados já existentes.
        candidate_columns={r[1] for r in con.execute("PRAGMA table_info(candidates)").fetchall()}
        for column,definition in (
            ("photo","TEXT"),("artistic_name","TEXT"),("mannequin","TEXT"),
            ("shoe_size","TEXT"),("body_size","TEXT")
        ):
            if column not in candidate_columns:
                con.execute(f"ALTER TABLE candidates ADD COLUMN {column} {definition}")
        con.execute("""CREATE TABLE IF NOT EXISTS arrivals (
          candidate_id INTEGER PRIMARY KEY, arrived_at TEXT NOT NULL, device TEXT DEFAULT 'Recepção',
          FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE)""")
        con.execute("""CREATE TABLE IF NOT EXISTS scores (
          id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL, evaluator TEXT NOT NULL,
          communication REAL, samba REAL, updated_at TEXT NOT NULL, UNIQUE(candidate_id,evaluator),
          FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE)""")
        con.execute("""CREATE TABLE IF NOT EXISTS evaluator_roster (
          stage TEXT NOT NULL, position INTEGER NOT NULL, evaluator TEXT NOT NULL,
          PRIMARY KEY(stage,position), UNIQUE(stage,evaluator))""")
        con.execute("""CREATE TABLE IF NOT EXISTS app_settings (
          setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS audition_result_publication (
          candidate_id INTEGER PRIMARY KEY, category TEXT NOT NULL, position INTEGER NOT NULL,
          published_at TEXT NOT NULL, FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE)""")
    elif target == CONTEST_DB.resolve():
        con.execute("""CREATE TABLE IF NOT EXISTS evaluator_roster (
          stage TEXT NOT NULL, position INTEGER NOT NULL, evaluator TEXT NOT NULL,
          PRIMARY KEY(stage,position), UNIQUE(stage,evaluator))""")
        con.execute("""CREATE TABLE IF NOT EXISTS contest_scores (
          id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL, evaluator TEXT NOT NULL,
          aesthetics_sympathy REAL, communication_elegance REAL, samba REAL, updated_at TEXT NOT NULL,
          UNIQUE(candidate_id,evaluator))""")
    else:
        con.close()
        raise ValueError("Banco de dados não reconhecido: "+str(target))
    return con

def ensure_contest_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS contest_participants (id INTEGER PRIMARY KEY AUTOINCREMENT, source_candidate_id INTEGER NOT NULL UNIQUE, category TEXT NOT NULL, name TEXT NOT NULL, artistic_name TEXT DEFAULT '', mannequin TEXT DEFAULT '', shoe_size TEXT DEFAULT '', body_size TEXT DEFAULT '', photo TEXT DEFAULT '', migrated_at TEXT DEFAULT CURRENT_TIMESTAMP, audition_total REAL, audition_samba REAL, audition_partial_total REAL, audition_complete_evaluators INTEGER DEFAULT 0, audition_evaluators_with_data INTEGER DEFAULT 0, source_position INTEGER)""")
    con.execute("""CREATE TABLE IF NOT EXISTS contest_arrivals (candidate_id INTEGER PRIMARY KEY, arrived_at TEXT NOT NULL, device TEXT DEFAULT 'Recepção', status TEXT DEFAULT 'PRESENTE')""")
    con.execute("CREATE TABLE IF NOT EXISTS app_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL)")
    cols={r[1] for r in con.execute("PRAGMA table_info(contest_participants)")}
    for name,definition in (("audition_total","REAL"),("audition_samba","REAL"),("audition_partial_total","REAL"),("audition_complete_evaluators","INTEGER DEFAULT 0"),("audition_evaluators_with_data","INTEGER DEFAULT 0"),("source_position","INTEGER")):
        if name not in cols: con.execute("ALTER TABLE contest_participants ADD COLUMN "+name+" "+definition)
    con.execute("""CREATE TABLE IF NOT EXISTS contest_scores (id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL, evaluator TEXT NOT NULL, aesthetics_sympathy REAL, communication_elegance REAL, samba REAL, updated_at TEXT NOT NULL, UNIQUE(candidate_id,evaluator))""")

def migrate_published_contest(audition_con, contest_con):
    ensure_contest_schema(contest_con)
    candidates=contest_eligible(audition_con)
    if len(candidates)!=25: raise ValueError("Migração cancelada: são necessários exatamente 25 classificados publicados; encontrados: "+str(len(candidates)))
    contest_con.execute("BEGIN IMMEDIATE")
    try:
        for c in candidates:
            contest_con.execute("""INSERT INTO contest_participants (source_candidate_id,category,name,artistic_name,mannequin,shoe_size,body_size,photo,audition_total,audition_samba,audition_partial_total,audition_complete_evaluators,audition_evaluators_with_data,source_position) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_candidate_id) DO UPDATE SET category=excluded.category,name=excluded.name,artistic_name=excluded.artistic_name,mannequin=excluded.mannequin,shoe_size=excluded.shoe_size,body_size=excluded.body_size,photo=excluded.photo,audition_total=excluded.audition_total,audition_samba=excluded.audition_samba,audition_partial_total=excluded.audition_partial_total,audition_complete_evaluators=excluded.audition_complete_evaluators,audition_evaluators_with_data=excluded.audition_evaluators_with_data,source_position=excluded.source_position""",
              (c["id"],c["category"],c["name"],c.get("artistic_name") or "",c.get("mannequin") or "",c.get("shoe_size") or "",c.get("body_size") or "",c.get("photo") or "",c.get("audition_total"),c.get("audition_samba"),c.get("audition_partial_total"),c.get("audition_complete_evaluators",0),c.get("audition_evaluators_with_data",0),c.get("position")))
        for score in contest_con.execute("SELECT id,candidate_id,evaluator FROM contest_scores ORDER BY id").fetchall():
            dest=contest_con.execute("SELECT id FROM contest_participants WHERE source_candidate_id=?",(score["candidate_id"],)).fetchone()
            if not dest or dest["id"]==score["candidate_id"]: continue
            exists=contest_con.execute("SELECT id FROM contest_scores WHERE candidate_id=? AND evaluator=?",(dest["id"],score["evaluator"])).fetchone()
            if exists: contest_con.execute("DELETE FROM contest_scores WHERE id=?",(score["id"],))
            else: contest_con.execute("UPDATE contest_scores SET candidate_id=? WHERE id=?",(dest["id"],score["id"]))
        contest_con.execute("INSERT INTO app_settings(setting_key,setting_value) VALUES('contest_roster_migrated','1') ON CONFLICT(setting_key) DO UPDATE SET setting_value='1'")
        contest_con.commit()
    except Exception: contest_con.rollback(); raise
    return len(candidates)

def contest_participant_rows(con):
    ensure_contest_schema(con)
    return [dict(r) for r in con.execute("""SELECT id,source_candidate_id,category,name,photo,artistic_name,mannequin,shoe_size,body_size,source_position AS position,audition_total,audition_samba,audition_partial_total,audition_complete_evaluators,audition_evaluators_with_data FROM contest_participants ORDER BY source_position,category COLLATE NOCASE,name COLLATE NOCASE""").fetchall()]

def parse_multipart(content_type,body):
    msg=BytesParser(policy=default).parsebytes(b"Content-Type: "+content_type.encode("latin-1")+b"\r\nMIME-Version: 1.0\r\n\r\n"+body)
    if not msg.is_multipart(): return None,None
    for part in msg.iter_parts():
        if "form-data" in part.get("Content-Disposition","") and part.get_param("name",header="content-disposition")=="file":
            return part.get_filename() or "candidatos.xlsx",part.get_payload(decode=True)
    return None,None


def contest_eligible(con):
    """Retorna somente os participantes oficialmente publicados para o Concurso.

    A lista de classificação publicada na Audição é a fonte de verdade. Ter
    notas de Audição, por si só, não classifica uma pessoa para o Concurso.
    A lista final é limitada a 25 participantes.
    """
    published=con.execute("""
        SELECT c.id,c.category,c.name,c.photo,c.artistic_name,c.mannequin,
               c.shoe_size,c.body_size,p.position
        FROM audition_result_publication p
        JOIN candidates c ON c.id=p.candidate_id
        ORDER BY p.position ASC, c.category COLLATE NOCASE, c.name COLLATE NOCASE
        LIMIT 25
    """).fetchall()
    if not published:
        return []
    ids=[r["id"] for r in published]
    marks=",".join("?" for _ in ids)
    rs=con.execute(
        f"SELECT candidate_id,evaluator,communication,samba FROM scores WHERE candidate_id IN ({marks})",
        ids
    ).fetchall()
    by={}
    for r in rs:
        by.setdefault(r["candidate_id"],{})[r["evaluator"]]={
            "communication":r["communication"],"samba":r["samba"]}
    chosen=[]
    for candidate in published:
        ev=by.get(candidate["id"],{})
        available=[v for v in ev.values()
                   if v["communication"] is not None or v["samba"] is not None]
        complete=[v for v in available
                  if v["communication"] is not None and v["samba"] is not None]
        audition_total=round(sum(v["communication"]+v["samba"] for v in complete),1) if complete else None
        audition_samba=round(sum(v["samba"] for v in complete),1) if complete else None
        partial_total=round(sum((v["communication"] or 0)+(v["samba"] or 0) for v in available),1)
        chosen.append({"id":candidate["id"],"category":candidate["category"],"name":candidate["name"],
            "photo":candidate["photo"],"artistic_name":candidate["artistic_name"],
            "mannequin":candidate["mannequin"],"shoe_size":candidate["shoe_size"],
            "body_size":candidate["body_size"],"position":candidate["position"],
            "audition_total":audition_total,"audition_samba":audition_samba,
            "audition_partial_total":partial_total,
            "audition_complete_evaluators":len(complete),
            "audition_evaluators_with_data":len(available)})
    return chosen

def contest_result_rows(audition_con, contest_con):
    eligible=contest_participant_rows(contest_con)
    evaluator_count=contest_con.execute(
        "SELECT COUNT(*) FROM evaluator_roster WHERE stage='CONCURSO' AND TRIM(evaluator)<>''"
    ).fetchone()[0]
    scores=contest_con.execute("SELECT candidate_id,evaluator,aesthetics_sympathy,communication_elegance,samba FROM contest_scores").fetchall()
    by={}
    for r in scores:
        by.setdefault(r["candidate_id"],{})[r["evaluator"]]={"aesthetics_sympathy":r["aesthetics_sympathy"],"communication_elegance":r["communication_elegance"],"samba":r["samba"]}
    output=[]
    for c in eligible:
        ev=by.get(c["id"],{})
        women=c["category"].strip().casefold()!="passista de ouro"
        required=("communication_elegance","samba","aesthetics_sympathy") if women else ("communication_elegance","samba")
        complete=[v for v in ev.values() if all(v[k] is not None for k in required)]
        # A finalização depende da quantidade de avaliadores cadastrados para o Concurso.
        vals={k:round(sum(v[k] for v in complete)/len(complete),2) if complete else None for k in required}
        partial_total=round(sum(vals[k] for k in required),2) if complete else None
        final= evaluator_count>0 and len(complete)>=evaluator_count
        status=("Aguardando cadastro de avaliadores" if evaluator_count==0 else
                "Final" if final else ("Parcial" if complete else "Aguardando notas"))
        output.append({"id":c["id"],"category":c["category"],"name":c["name"],
            "aesthetics_sympathy":vals.get("aesthetics_sympathy"),"communication_elegance":vals.get("communication_elegance"),
            "samba":vals.get("samba"),"total":partial_total,"partial_total":partial_total,
            "complete_evaluators":len(complete),"expected_evaluators":evaluator_count,
            "complete":final,"status":status,
            "audition_total":c["audition_total"],"title":None})
    # Only complete contest entries can be ranked; tie-break is higher mean Samba no Pé.
    for cat in ("Passista de Ouro","Diva do Samba (Plus Size)","Rainha do Samba"):
        group=[x for x in output if x["category"].strip().casefold()==cat.casefold() and x["complete"]]
        group.sort(key=lambda x:(-x["total"],-(x["samba"] or 0),x["name"].casefold()))
        for i,x in enumerate(group,1):
            if cat=="Rainha do Samba": x["title"]={1:"Rainha do Carnaval",2:"Primeira Princesa",3:"Segunda Princesa"}.get(i,f"{i}º lugar")
            elif cat=="Diva do Samba (Plus Size)": x["title"]="Diva Plus Size" if i==1 else f"{i}º lugar"
            else: x["title"]={1:"Passista de Ouro",2:"Passista de Prata"}.get(i,f"{i}º lugar")
    output.sort(key=lambda x:(x["category"].casefold(),x["title"] is None,x["title"] or "",x["name"].casefold()))
    return output

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(HERE),**kw)
    def send_json(self,data,status=200):
        body=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body)));self.send_header("Cache-Control","no-store")
        self.end_headers();self.wfile.write(body)
    def read_json(self):
        length=int(self.headers.get("content-length","0"))
        if length>1024*1024: raise ValueError("Requisição muito grande.")
        return json.loads(self.rfile.read(length).decode("utf-8"))
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/api/health": return self.send_json({"ok":True,"service":"UESP 2027","databases":{"audition":AUDITION_DB.name,"contest":CONTEST_DB.name}})
        if path=="/api/active-stage":
            with dbconn(AUDITION_DB) as con:
                row=con.execute("SELECT setting_value FROM app_settings WHERE setting_key='active_stage'").fetchone()
            return self.send_json({"ok":True,"stage":row["setting_value"] if row else "AUDICAO"})
        if path=="/api/evaluators":
            from urllib.parse import parse_qs
            stage=(parse_qs(urlparse(self.path).query).get("stage",["AUDICAO"])[0] or "AUDICAO").upper()
            if stage not in ("AUDICAO","CONCURSO"):
                return self.send_json({"ok":False,"error":"Etapa inválida."},400)
            database=CONTEST_DB if stage=="CONCURSO" else AUDITION_DB
            with dbconn(database) as con:
                rows=con.execute("SELECT evaluator FROM evaluator_roster WHERE stage=? ORDER BY position",(stage,)).fetchall()
                # Migração compatível: se a lista da Audição ainda não estiver no
                # banco novo, recupera apenas os nomes do banco legado, sem alterar
                # nem apagar o arquivo original. O Concurso permanece independente.
                if not rows and stage=="AUDICAO":
                    legacy=HERE/"uesp_2027.db"
                    if legacy.exists() and legacy.resolve() not in (AUDITION_DB.resolve(),CONTEST_DB.resolve()):
                        try:
                            with sqlite3.connect(legacy,timeout=5) as old_con:
                                exists=old_con.execute(
                                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evaluator_roster'"
                                ).fetchone()
                                if exists:
                                    old_rows=old_con.execute(
                                        "SELECT evaluator FROM evaluator_roster WHERE stage='AUDICAO' ORDER BY position"
                                    ).fetchall()
                                    names=[]; seen=set()
                                    for old_row in old_rows:
                                        name=" ".join(str(old_row[0] or "").split())
                                        key=name.casefold()
                                        if name and key not in seen:
                                            names.append(name); seen.add(key)
                                    if names:
                                        con.executemany(
                                            "INSERT OR IGNORE INTO evaluator_roster(stage,position,evaluator) VALUES('AUDICAO',?,?)",
                                            [(i,name) for i,name in enumerate(names,1)]
                                        )
                                        con.commit()
                                        rows=con.execute(
                                            "SELECT evaluator FROM evaluator_roster WHERE stage=? ORDER BY position",
                                            (stage,)
                                        ).fetchall()
                        except sqlite3.Error:
                            # A falha na leitura do banco legado não deve derrubar a API.
                            pass
            return self.send_json({"ok":True,"stage":stage,"evaluators":[r["evaluator"] for r in rows]})
        if path=="/api/arrival-candidates":
            with dbconn(CONTEST_DB) as con: candidates=contest_participant_rows(con)
            if not candidates:
                with dbconn(AUDITION_DB) as con: candidates=contest_eligible(con)
            return self.send_json({"ok":True,"candidates":candidates})
        if path=="/api/contest-report-data":
            with dbconn(CONTEST_DB) as contest_con:
                candidates=contest_participant_rows(contest_con)
                rows=contest_con.execute("SELECT candidate_id,evaluator,aesthetics_sympathy,communication_elegance,samba,updated_at FROM contest_scores").fetchall()
            scores={}
            for r in rows:
                scores.setdefault(str(r["candidate_id"]),{})[r["evaluator"]]={"aesthetics_sympathy":r["aesthetics_sympathy"],"communication_elegance":r["communication_elegance"],"samba":r["samba"],"updated_at":r["updated_at"]}
            return self.send_json({"ok":True,"candidates":candidates,"scores":scores})
        if path=="/api/candidates":
            with dbconn() as con:
                rows=con.execute("""SELECT id,category,name,photo,artistic_name,mannequin,shoe_size,body_size
                    FROM candidates ORDER BY category,name COLLATE NOCASE""").fetchall()
            return self.send_json({"ok":True,"candidates":[dict(x) for x in rows]})
        if path=="/api/arrivals":
            with dbconn(CONTEST_DB) as con:
                ensure_contest_schema(con)
                migrated=con.execute("SELECT COUNT(*) FROM contest_participants").fetchone()[0]
                rows=con.execute("SELECT candidate_id,arrived_at,device,status FROM contest_arrivals").fetchall()
            if migrated: return self.send_json({"ok":True,"arrivals":{str(r["candidate_id"]):{"arrived_at":r["arrived_at"],"device":r["device"],"status":r["status"]} for r in rows}})
            with dbconn() as con: rows=con.execute("SELECT candidate_id,arrived_at,device FROM arrivals").fetchall()
            return self.send_json({"ok":True,"arrivals":{str(r["candidate_id"]):{"arrived_at":r["arrived_at"],"device":r["device"]} for r in rows}})
        if path=="/api/scores":
            with dbconn() as con:
                rows=con.execute("SELECT candidate_id,evaluator,communication,samba,updated_at FROM scores").fetchall()
            scores={}
            for r in rows: scores.setdefault(str(r["candidate_id"]),{})[r["evaluator"]]={"communication":r["communication"],"samba":r["samba"],"updated_at":r["updated_at"]}
            return self.send_json({"ok":True,"scores":scores})
        if path=="/api/contest-candidates":
            with dbconn(CONTEST_DB) as contest_con:
                candidates=contest_participant_rows(contest_con)
                rs=contest_con.execute("SELECT candidate_id,evaluator,aesthetics_sympathy,communication_elegance,samba,updated_at FROM contest_scores").fetchall()
            saved={}
            for r in rs:
                saved.setdefault(str(r["candidate_id"]),{})[r["evaluator"]]={"aesthetics_sympathy":r["aesthetics_sympathy"],"communication_elegance":r["communication_elegance"],"samba":r["samba"],"updated_at":r["updated_at"]}
            return self.send_json({"ok":True,"candidates":candidates,"scores":saved})
        if path=="/api/export-xlsx":
            try:
                import openpyxl
                from openpyxl.styles import Font, PatternFill
                wb=openpyxl.Workbook()
                ws=wb.active; ws.title="Chegadas"
                ws.append(["Categoria","Participante","Chegada","Dispositivo","Status"])
                with dbconn(AUDITION_DB) as con:
                    rows=con.execute("""SELECT c.category,c.name,a.arrived_at,a.device
                        FROM candidates c LEFT JOIN arrivals a ON a.candidate_id=c.id
                        ORDER BY c.category,c.name COLLATE NOCASE""").fetchall()
                for r in rows:
                    ws.append([r["category"],r["name"],r["arrived_at"] or "",r["device"] or "", "PRESENTE" if r["arrived_at"] else "PENDENTE"])
                for cell in ws[1]:
                    cell.font=Font(bold=True,color="FFFFFF");cell.fill=PatternFill("solid",fgColor="C8102E")
                ws.freeze_panes="A2";ws.auto_filter.ref=ws.dimensions
                for col in ws.columns:
                    letter=col[0].column_letter
                    ws.column_dimensions[letter].width=min(42,max(14,max(len(str(cell.value or "")) for cell in col)+2))
                out=io.BytesIO();wb.save(out);wb.close();body=out.getvalue()
                self.send_response(200);self.send_header("Content-Type","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition",'attachment; filename="controle_chegadas_uesp.xlsx"')
                self.send_header("Content-Length",str(len(body)));self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(body);return
            except ImportError:
                return self.send_json({"ok":False,"error":"Falta openpyxl. Execute python -m pip install openpyxl."},500)
            except Exception as e:
                return self.send_json({"ok":False,"error":"Não foi possível exportar o Excel: "+str(e)},500)
        if path=="/api/contest-results":
            with dbconn(CONTEST_DB) as contest_con: results=contest_result_rows(None,contest_con)
            return self.send_json({"ok":True,"results":results})
        if path=="/api/results":
            with dbconn() as con:
                cs=con.execute("""
                    SELECT c.id,c.category,c.name
                    FROM candidates c
                    INNER JOIN arrivals a ON a.candidate_id=c.id
                    ORDER BY c.category,c.name COLLATE NOCASE
                """).fetchall()
                rs=con.execute("""
                    SELECT candidate_id,evaluator,communication,samba,updated_at
                    FROM scores
                """).fetchall()
            by={}
            for r in rs:
                by.setdefault(r["candidate_id"],{})[r["evaluator"]]={
                    "communication":r["communication"],
                    "samba":r["samba"],
                    "updated_at":r["updated_at"]
                }
            out=[]
            for c in cs:
                ev=by.get(c["id"],{})
                complete=[
                    v for v in ev.values()
                    if v["communication"] is not None and v["samba"] is not None
                ]
                partial_total=round(sum(v["communication"]+v["samba"] for v in complete),1)
                count=len(complete)
                out.append({
                    "id":c["id"],
                    "category":c["category"],
                    "name":c["name"],
                    "complete_evaluators":count,
                    "complete":count>=5,
                    "partial_total":partial_total if count else None,
                    "final_total":partial_total if count>=5 else None,
                    "status":"Completa" if count>=5 else ("Parcial" if count else "Aguardando notas")
                })
            return self.send_json({"ok":True,"results":out})
        return super().do_GET()
    def do_POST(self):
        path=urlparse(self.path).path
        if path=="/api/active-stage":
            try: data=self.read_json()
            except Exception as e: return self.send_json({"ok":False,"error":"Dados inválidos: "+str(e)},400)
            stage=str(data.get("stage","")).strip().upper()
            if stage not in ("AUDICAO","CONCURSO"):
                return self.send_json({"ok":False,"error":"Etapa deve ser AUDICAO ou CONCURSO."},400)
            with dbconn(AUDITION_DB) as con:
                con.execute("INSERT INTO app_settings(setting_key,setting_value) VALUES('active_stage',?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",(stage,))
                con.commit()
            return self.send_json({"ok":True,"stage":stage})
        if path=="/api/evaluators":
            try: data=self.read_json()
            except Exception as e: return self.send_json({"ok":False,"error":"Dados inválidos: "+str(e)},400)
            stage=str(data.get("stage","AUDICAO")).strip().upper()
            names=data.get("evaluators",[])
            if stage not in ("AUDICAO","CONCURSO") or not isinstance(names,list):
                return self.send_json({"ok":False,"error":"Etapa ou lista de avaliadores inválida."},400)
            cleaned=[]; seen=set()
            for value in names:
                name=" ".join(str(value or "").split())
                key=name.casefold()
                if not name: continue
                if len(name)>80: return self.send_json({"ok":False,"error":"Cada nome pode ter até 80 caracteres."},400)
                if key not in seen:
                    cleaned.append(name); seen.add(key)
            if len(cleaned)>100: return self.send_json({"ok":False,"error":"Máximo de 100 avaliadores por etapa."},400)
            database=CONTEST_DB if stage=="CONCURSO" else AUDITION_DB
            with dbconn(database) as con:
                con.execute("DELETE FROM evaluator_roster WHERE stage=?",(stage,))
                con.executemany("INSERT INTO evaluator_roster(stage,position,evaluator) VALUES(?,?,?)",[(stage,i,name) for i,name in enumerate(cleaned,1)])
                con.commit()
            return self.send_json({"ok":True,"stage":stage,"evaluators":cleaned,"saved":len(cleaned)})
        if path=="/api/clear-contest-scores":
            try:
                data=self.read_json()
            except Exception as e:
                return self.send_json({"ok":False,"error":"Confirmação inválida: "+str(e)},400)
            if not isinstance(data,dict) or data.get("confirm") is not True:
                return self.send_json({"ok":False,"error":"A exclusão exige confirmação explícita."},400)
            try:
                with dbconn(CONTEST_DB) as con:
                    deleted=con.execute("SELECT COUNT(*) FROM contest_scores").fetchone()[0]
                    con.execute("DELETE FROM contest_scores")
                    con.commit()
                return self.send_json({"ok":True,"deleted":deleted,"message":"Somente as notas do Concurso foram removidas; avaliadores e dados da Audição foram preservados."})
            except Exception as e:
                return self.send_json({"ok":False,"error":"Não foi possível limpar as notas do Concurso: "+str(e)},500)
        if path=="/api/clear-scores":
            # Os nomes dos avaliadores são armazenados na própria tabela scores.
            # Esta operação remove avaliações e nomes, preservando candidatos e chegadas.
            try:
                with dbconn() as con:
                    deleted=con.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
                    con.execute("DELETE FROM scores")
                    con.commit()
                return self.send_json({"ok":True,"deleted":deleted,"message":"Notas e registros de avaliadores excluídos. Candidatos e chegadas foram preservados."})
            except Exception as e:
                return self.send_json({"ok":False,"error":"Não foi possível excluir as notas: "+str(e)},500)
        if path=="/api/publish-audition-results":
            try:
                data=self.read_json()
                participants=data.get("participants", [])
                if not isinstance(participants, list):
                    return self.send_json({"ok":False,"error":"Lista de resultados inválida."},400)
                now=datetime.now().astimezone().isoformat(timespec="seconds")
                published=[]
                with dbconn() as con:
                    for item in participants:
                        if not isinstance(item, dict):
                            continue
                        cid=item.get("candidate_id")
                        if not isinstance(cid,int):
                            continue
                        row=con.execute("SELECT id,category FROM candidates WHERE id=?",(cid,)).fetchone()
                        if not row:
                            continue
                        cat=(row["category"] or "").strip()
                        if cat.casefold() not in ("rainha do samba","diva do samba (plus size)","passista de ouro"):
                            continue
                        # Só publica participantes que tenham ao menos uma nota de Audição.
                        has_score=con.execute("SELECT 1 FROM scores WHERE candidate_id=? AND (communication IS NOT NULL OR samba IS NOT NULL) LIMIT 1",(cid,)).fetchone()
                        if not has_score:
                            continue
                        try: position=max(1,int(item.get("position",len(published)+1)))
                        except (TypeError,ValueError): position=len(published)+1
                        published.append((cid,cat,position,now))
                    con.execute("DELETE FROM audition_result_publication")
                    con.executemany("INSERT INTO audition_result_publication(candidate_id,category,position,published_at) VALUES(?,?,?,?)",published)
                    con.commit()
                with dbconn(AUDITION_DB) as audition_con, dbconn(CONTEST_DB) as contest_con: migrated=migrate_published_contest(audition_con,contest_con)
                return self.send_json({"ok":True,"published":len(published),"migrated":migrated,"published_at":now,"message":"Resultado publicado e classificados migrados para o banco independente do Concurso."})
            except Exception as e:
                return self.send_json({"ok":False,"error":"Não foi possível disponibilizar os resultados da Audição: "+str(e)},500)
        if path=="/api/contest-score":
            try: data=self.read_json()
            except Exception as e: return self.send_json({"ok":False,"error":"Dados inválidos: "+str(e)},400)
            cid=data.get("candidate_id"); evaluator=str(data.get("evaluator","")).strip()
            aesthetics=data.get("aesthetics_sympathy"); communication=data.get("communication_elegance"); samba=data.get("samba")
            if not isinstance(cid,int): return self.send_json({"ok":False,"error":"Participante inválido."},400)
            if not evaluator or len(evaluator)>80: return self.send_json({"ok":False,"error":"Informe o nome do avaliador (até 80 caracteres)."},400)
            def valid_contest(v):
                if v is None or v=="": return True
                try:
                    n=float(v); return 9<=n<=10 and abs(round(n*10)-n*10)<1e-7
                except Exception: return False
            if not all(valid_contest(v) for v in (aesthetics,communication,samba)):
                return self.send_json({"ok":False,"error":"Notas de 9,0 a 10,0 em intervalos de 0,1."},400)
            aesthetics=float(aesthetics) if aesthetics not in (None,"") else None
            communication=float(communication) if communication not in (None,"") else None
            samba=float(samba) if samba not in (None,"") else None
            with dbconn(CONTEST_DB) as contest_con:
                eligible={x["id"]:x for x in contest_participant_rows(contest_con)}
                if cid not in eligible: return self.send_json({"ok":False,"error":"Participante não está na lista migrada do Concurso."},400)
                if eligible[cid]["category"].strip().casefold()=="passista de ouro": aesthetics=None
                now=datetime.now().astimezone().isoformat(timespec="seconds")
                # Atualiza somente o critério enviado; notas de outros critérios devem ser preservadas.
                fields=[]
                values=[]
                for key,column,value in (
                    ("aesthetics_sympathy","aesthetics_sympathy",aesthetics),
                    ("communication_elegance","communication_elegance",communication),
                    ("samba","samba",samba)):
                    if key in data:
                        fields.append(column+"=excluded."+column)
                        values.append(value)
                if not fields:
                    return self.send_json({"ok":False,"error":"Nenhum critério de nota foi informado."},400)
                contest_con.execute(
                    "INSERT INTO contest_scores(candidate_id,evaluator,aesthetics_sympathy,communication_elegance,samba,updated_at) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(candidate_id,evaluator) DO UPDATE SET "
                    + ",".join(fields)+",updated_at=excluded.updated_at",
                    (cid,evaluator,aesthetics,communication,samba,now))
                contest_con.commit()
            return self.send_json({"ok":True,"updated_at":now})
        if path=="/api/score":
            try: data=self.read_json()
            except Exception as e: return self.send_json({"ok":False,"error":"Dados inválidos: "+str(e)},400)
            cid=data.get("candidate_id"); evaluator=str(data.get("evaluator","")).strip()
            communication=data.get("communication"); samba=data.get("samba")
            if not isinstance(cid,int): return self.send_json({"ok":False,"error":"Candidata inválida."},400)
            if not evaluator or len(evaluator)>80: return self.send_json({"ok":False,"error":"Informe o nome do avaliador (até 80 caracteres)."},400)
            def valid(v):
                if v is None or v=="": return True
                try:
                    n=float(v); return 9<=n<=10 and abs(round(n*10)-n*10)<1e-7
                except Exception: return False
            if not valid(communication) or not valid(samba): return self.send_json({"ok":False,"error":"Notas de 9,0 a 10,0 em intervalos de 0,1."},400)
            communication=float(communication) if communication not in (None,"") else None
            samba=float(samba) if samba not in (None,"") else None
            with dbconn() as con:
                if not con.execute("SELECT id FROM candidates WHERE id=?",(cid,)).fetchone(): return self.send_json({"ok":False,"error":"Candidata não encontrada."},404)
                now=datetime.now().astimezone().isoformat(timespec="seconds")
                con.execute("INSERT INTO scores(candidate_id,evaluator,communication,samba,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(candidate_id,evaluator) DO UPDATE SET communication=excluded.communication,samba=excluded.samba,updated_at=excluded.updated_at",(cid,evaluator,communication,samba,now)); con.commit()
            return self.send_json({"ok":True,"updated_at":now})
        if path=="/api/arrival":
            try: data=self.read_json()
            except Exception as e: return self.send_json({"ok":False,"error":"Dados inválidos: "+str(e)},400)
            cid=data.get("candidate_id")
            if not isinstance(cid,int): return self.send_json({"ok":False,"error":"Candidato inválido."},400)
            present=bool(data.get("present"))
            absent=bool(data.get("absent"))
            save_only=bool(data.get("save_only"))
            photo=data.get("photo")
            if photo is not None:
                if not isinstance(photo,str) or not photo.startswith("data:image/") or len(photo)>8*1024*1024:
                    return self.send_json({"ok":False,"error":"Foto inválida ou muito grande (máximo 8 MB)."},400)
            with dbconn(CONTEST_DB) as contest_con:
                contest_person=contest_con.execute("SELECT id FROM contest_participants WHERE id=?",(cid,)).fetchone()
            if contest_person:
                with dbconn(CONTEST_DB) as con:
                    fields=("artistic_name","mannequin","shoe_size","body_size"); assignments=[]; values=[]
                    for field in fields:
                        if field in data: assignments.append(field+"=?"); values.append(" ".join(str(data.get(field) or "").split())[:120])
                    if photo is not None: assignments.append("photo=?"); values.append(photo)
                    if assignments: con.execute("UPDATE contest_participants SET "+", ".join(assignments)+" WHERE id=?",(*values,cid))
                    if save_only:
                        con.commit(); return self.send_json({"ok":True,"saved":True,"photo_saved":photo is not None})
                    if present or absent:
                        now=datetime.now().astimezone().isoformat(timespec="seconds"); status="PRESENTE" if present else "AUSENTE"; device=data.get("device","Recepção") if present else "AUSENTE"
                        con.execute("INSERT INTO contest_arrivals(candidate_id,arrived_at,device,status) VALUES(?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET arrived_at=excluded.arrived_at,device=excluded.device,status=excluded.status",(cid,now,device,status))
                        con.commit(); return self.send_json({"ok":True,"arrival":{"arrived_at":now,"device":device,"status":status}})
                    con.execute("DELETE FROM contest_arrivals WHERE candidate_id=?",(cid,)); con.commit(); return self.send_json({"ok":True,"arrival":None})
            with dbconn() as con:
                exists=con.execute("SELECT id FROM candidates WHERE id=?",(cid,)).fetchone()
                if not exists:return self.send_json({"ok":False,"error":"Candidato não encontrado."},404)
                fields=("artistic_name","mannequin","shoe_size","body_size")
                assignments=[]; values=[]
                for field in fields:
                    if field in data:
                        assignments.append(field+"=?")
                        values.append(" ".join(str(data.get(field) or "").split())[:120])
                if photo is not None:
                    assignments.append("photo=?"); values.append(photo)
                if assignments:
                    con.execute("UPDATE candidates SET "+", ".join(assignments)+" WHERE id=?",(*values,cid))
                if save_only:
                    con.commit()
                    return self.send_json({"ok":True,"saved":True,"photo_saved":photo is not None})
                if present:
                    now=datetime.now().astimezone().isoformat(timespec="seconds")
                    con.execute("INSERT INTO arrivals(candidate_id,arrived_at,device) VALUES(?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET arrived_at=excluded.arrived_at,device=excluded.device",(cid,now,data.get("device","Recepção")))
                    row=con.execute("SELECT candidate_id,arrived_at,device FROM arrivals WHERE candidate_id=?",(cid,)).fetchone()
                    con.commit()
                    return self.send_json({"ok":True,"arrival":{"arrived_at":row["arrived_at"],"device":row["device"],"status":"PRESENTE"}})
                if absent:
                    now=datetime.now().astimezone().isoformat(timespec="seconds")
                    con.execute("INSERT INTO arrivals(candidate_id,arrived_at,device) VALUES(?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET arrived_at=excluded.arrived_at,device=excluded.device",(cid,now,"AUSENTE"))
                    con.commit()
                    return self.send_json({"ok":True,"arrival":{"arrived_at":now,"device":"AUSENTE","status":"AUSENTE"}})
                con.execute("DELETE FROM arrivals WHERE candidate_id=?",(cid,));con.commit()
                return self.send_json({"ok":True,"arrival":None})
        if path!="/api/import": return self.send_json({"ok":False,"error":"Rota não encontrada"},404)
        ctype=self.headers.get("content-type","")
        try:
            length=int(self.headers.get("content-length","0"))
            if length<=0 or length>50*1024*1024:return self.send_json({"ok":False,"error":"Arquivo vazio ou maior que 50 MB."},400)
            filename,raw=parse_multipart(ctype,self.rfile.read(length))
            if not raw:return self.send_json({"ok":False,"error":"Arquivo não recebido."},400)
            import openpyxl
            wb=openpyxl.load_workbook(io.BytesIO(raw),read_only=True,data_only=True);ws=wb[wb.sheetnames[0]]
            rows=[];skipped=0
            for row in ws.iter_rows(min_row=2,values_only=True):
                cat=str(row[0]).strip() if len(row)>0 and row[0] is not None else ""
                name=str(row[1]).strip() if len(row)>1 and row[1] is not None else ""
                if not cat and not name:continue
                if not cat or not name:skipped+=1;continue
                rows.append((cat,name))
            wb.close()
            if not rows:return self.send_json({"ok":False,"error":"Nenhum candidato válido nas colunas A e B."},400)
            with dbconn() as con:
                # Reimportação substitui candidatos e limpa presenças antigas para evitar associação incorreta.
                con.execute("DELETE FROM arrivals");con.execute("DELETE FROM scores");con.execute("DELETE FROM audition_result_publication");con.execute("DELETE FROM candidates")
                con.executemany("INSERT OR IGNORE INTO candidates(category,name) VALUES (?,?)",rows);con.commit()
                grouped=con.execute("SELECT category,COUNT(*) n FROM candidates GROUP BY category").fetchall()
            return self.send_json({"ok":True,"imported":sum(x["n"] for x in grouped),"counts":{x["category"]:x["n"] for x in grouped},"skipped":skipped})
        except ImportError:return self.send_json({"ok":False,"error":"Falta openpyxl. Execute python -m pip install openpyxl"},500)
        except Exception as e:return self.send_json({"ok":False,"error":"Erro ao importar Excel: "+str(e)},400)
    def log_message(self,fmt,*args):print("%s - %s"%(self.address_string(),fmt%args))

def local_ip():
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try:s.connect(("192.0.2.1",80));return s.getsockname()[0]
    except Exception:return "IP-DO-NOTEBOOK"
    finally:s.close()

def init_contest_db():
    with dbconn(CONTEST_DB) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS contest_scores (
          id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL, evaluator TEXT NOT NULL,
          aesthetics_sympathy REAL, communication_elegance REAL, samba REAL, updated_at TEXT NOT NULL,
          UNIQUE(candidate_id,evaluator))""")
        con.commit()

if __name__=="__main__":
    with dbconn(AUDITION_DB): pass
    init_contest_db()
    try:
        with dbconn(AUDITION_DB) as audition_con, dbconn(CONTEST_DB) as contest_con:
            if audition_con.execute("SELECT COUNT(*) FROM audition_result_publication").fetchone()[0]: migrate_published_contest(audition_con,contest_con)
    except Exception as e: print("Aviso: migração do Concurso não concluída: "+str(e))
    ip=local_ip()
    print("\nCentral UESP 2027 iniciada.")
    print("No notebook: http://127.0.0.1:8765/central.html")
    print(f"Controle de chegada: http://127.0.0.1:8765/chegada.html")
    print(f"Na rede Wi-Fi: http://{ip}:8765/central.html")
    print(f"Chegada Rainha: http://{ip}:8765/chegada.html?categoria=rainha")
    print(f"Chegada Passista: http://{ip}:8765/chegada.html?categoria=passista")
    print(f"Chegada Diva: http://{ip}:8765/chegada.html?categoria=diva")
    print("Mantenha esta janela aberta. Ctrl+C encerra o servidor.\n")
    ThreadingHTTPServer(("0.0.0.0",8765),Handler).serve_forever()
