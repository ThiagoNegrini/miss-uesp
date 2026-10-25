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
DB=HERE/"uesp_2027.db"
CATEGORIES=["Rainha do Samba","Passista de Ouro","Diva do Samba (Plus Size)"]

# Serialize schema initialization across request threads. SQLite still handles normal
# reads/writes with a busy timeout; WAL improves reader/writer coexistence.
import threading
_DB_INIT_LOCK = threading.RLock()

class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()

def dbconn():
    con=sqlite3.connect(DB,timeout=30,factory=_ClosingConnection)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    # The journal mode is persistent; setting it on every connection can itself
    # contend with other requests, so it is configured during server startup.
    return con

def init_db():
    # Run schema creation and additive migrations once, before the threaded server
    # starts accepting requests. Keep the database's existing records untouched.
    with _DB_INIT_LOCK:
        con=sqlite3.connect(DB,timeout=30,factory=_ClosingConnection)
        con.row_factory=sqlite3.Row
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS candidates (
          id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, name TEXT NOT NULL,
          artistic_name TEXT DEFAULT '', mannequin TEXT DEFAULT '', shoe_size TEXT DEFAULT '', body_size TEXT DEFAULT '', photo TEXT DEFAULT '',
          imported_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(category,name))""")
        cols={r['name'] for r in con.execute('PRAGMA table_info(candidates)').fetchall()}
        for col in ('artistic_name','mannequin','shoe_size','body_size','photo'):
            if col not in cols: con.execute(f"ALTER TABLE candidates ADD COLUMN {col} TEXT DEFAULT ''")
        con.execute("CREATE TABLE IF NOT EXISTS import_template (id INTEGER PRIMARY KEY CHECK(id=1), filename TEXT, content BLOB)")
        con.execute("""CREATE TABLE IF NOT EXISTS arrivals (
          candidate_id INTEGER PRIMARY KEY, arrived_at TEXT NOT NULL, device TEXT DEFAULT 'Recepção',
          status TEXT NOT NULL DEFAULT 'PRESENTE',
          FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE)""")
        arrival_cols={r['name'] for r in con.execute('PRAGMA table_info(arrivals)').fetchall()}
        if 'status' not in arrival_cols:
            con.execute("ALTER TABLE arrivals ADD COLUMN status TEXT NOT NULL DEFAULT 'PRESENTE'")
        con.execute("""CREATE TABLE IF NOT EXISTS scores (
          id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL, evaluator TEXT NOT NULL,
          communication REAL, samba REAL, updated_at TEXT NOT NULL, UNIQUE(candidate_id,evaluator),
          FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE)""")
        con.execute("""CREATE TABLE IF NOT EXISTS contest_scores (
          id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL, evaluator TEXT NOT NULL,
          aesthetics_sympathy REAL, communication_elegance REAL, samba REAL, updated_at TEXT NOT NULL,
          UNIQUE(candidate_id,evaluator), FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE)""")
        con.execute("""CREATE TABLE IF NOT EXISTS audition_result_publication (
          candidate_id INTEGER PRIMARY KEY, category TEXT NOT NULL, position INTEGER NOT NULL,
          published_at TEXT NOT NULL, FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE)""")
        con.execute("""CREATE TABLE IF NOT EXISTS evaluator_roster (
          stage TEXT NOT NULL CHECK(stage IN ('AUDICAO','CONCURSO')),
          evaluator TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(stage,evaluator))""")
        for stage, table in (("AUDICAO", "scores"), ("CONCURSO", "contest_scores")):
            if con.execute("SELECT 1 FROM evaluator_roster WHERE stage=? LIMIT 1", (stage,)).fetchone() is None:
                names=con.execute(f"SELECT DISTINCT evaluator FROM {table} ORDER BY evaluator COLLATE NOCASE").fetchall()
                con.executemany("INSERT OR IGNORE INTO evaluator_roster(stage,evaluator,position) VALUES(?,?,?)",
                                [(stage, row[0], i) for i,row in enumerate(names,1)])
        con.execute("CREATE TABLE IF NOT EXISTS active_stage (id INTEGER PRIMARY KEY CHECK(id=1), stage TEXT NOT NULL CHECK(stage IN ('AUDICAO','CONCURSO')))")
        con.execute("INSERT OR IGNORE INTO active_stage(id,stage) VALUES(1,'AUDICAO')")
        con.commit()

def parse_multipart(content_type,body):
    msg=BytesParser(policy=default).parsebytes(b"Content-Type: "+content_type.encode("latin-1")+b"\r\nMIME-Version: 1.0\r\n\r\n"+body)
    if not msg.is_multipart(): return None,None
    for part in msg.iter_parts():
        if "form-data" in part.get("Content-Disposition","") and part.get_param("name",header="content-disposition")=="file":
            return part.get_filename() or "candidatos.xlsx",part.get_payload(decode=True)
    return None,None


def contest_eligible(con):
    """Retorna participantes presentes com cinco avaliações completas, dentro das vagas oficiais.

    A lista da segunda etapa é calculada diretamente das notas da Audição:
    5 Divas, 6 Passistas e 14 Rainhas. Os registros de notas não são alterados.
    """
    import unicodedata

    def normalize(value):
        return " ".join(unicodedata.normalize("NFKD", (value or ""))
                        .encode("ascii", "ignore").decode("ascii").casefold().split())

    category_names = {
        "rainha do samba": "Rainha do Samba",
        "diva do samba plus size": "Diva do Samba (Plus Size)",
        "diva do samba (plus size)": "Diva do Samba (Plus Size)",
        "passista de ouro": "Passista de Ouro",
    }
    quotas = {"rainha do samba": 14, "diva do samba (plus size)": 5, "passista de ouro": 6}
    candidates = con.execute("""SELECT c.id,c.category,c.name,c.photo
        FROM candidates c
        JOIN arrivals a ON a.candidate_id=c.id
        WHERE COALESCE(a.status,'PRESENTE')='PRESENTE'
        ORDER BY c.category,c.name COLLATE NOCASE""").fetchall()
    rows = con.execute("SELECT candidate_id,evaluator,communication,samba FROM scores").fetchall()
    by = {}
    for row in rows:
        by.setdefault(row["candidate_id"], {})[row["evaluator"]] = {
            "communication": row["communication"], "samba": row["samba"]
        }

    by_category = {}
    for candidate in candidates:
        normalized = normalize(candidate["category"])
        if normalized not in category_names:
            continue
        evaluator_scores = by.get(candidate["id"], {})
        complete = [score for score in evaluator_scores.values()
                    if score["communication"] is not None and score["samba"] is not None]
        if len(complete) < 5:
            continue
        total = round(sum(score["communication"] + score["samba"] for score in complete), 1)
        samba_total = round(sum(score["samba"] for score in complete), 1)
        by_category.setdefault(normalized, []).append({
            "id": candidate["id"], "category": category_names[normalized],
            "name": candidate["name"], "photo": candidate["photo"],
            "audition_total": total, "audition_samba": samba_total,
            "audition_partial_total": total, "audition_complete_evaluators": len(complete),
            "audition_evaluators_with_data": len(evaluator_scores)
        })

    chosen = []
    for normalized, group in by_category.items():
        group.sort(key=lambda item: (-item["audition_total"], -item["audition_samba"], item["name"].casefold()))
        chosen.extend(group[:quotas[normalized]])
    chosen.sort(key=lambda item: (item["category"].casefold(), -item["audition_total"],
                                  -item["audition_samba"], item["name"].casefold()))
    return chosen


def contest_result_rows(con):
    eligible=contest_eligible(con)
    scores=con.execute("SELECT candidate_id,evaluator,aesthetics_sympathy,communication_elegance,samba FROM contest_scores").fetchall()
    by={}
    for r in scores:
        by.setdefault(r["candidate_id"],{})[r["evaluator"]]={"aesthetics_sympathy":r["aesthetics_sympathy"],"communication_elegance":r["communication_elegance"],"samba":r["samba"]}
    output=[]
    for c in eligible:
        ev=by.get(c["id"],{})
        women=c["category"].strip().casefold()!="passista de ouro"
        required=("communication_elegance","samba","aesthetics_sympathy") if women else ("communication_elegance","samba")
        complete=[v for v in ev.values() if all(v[k] is not None for k in required)]
        # Partial results are available as soon as at least one evaluator has saved all applicable criteria.
        # The total is the sum of the current mean of each criterion; it becomes final after five evaluators.
        vals={k:round(sum(v[k] for v in complete)/len(complete),2) if complete else None for k in required}
        partial_total=round(sum(vals[k] for k in required),2) if complete else None
        final= len(complete)>=5
        output.append({"id":c["id"],"category":c["category"],"name":c["name"],
            "aesthetics_sympathy":vals.get("aesthetics_sympathy"),"communication_elegance":vals.get("communication_elegance"),
            "samba":vals.get("samba"),"total":partial_total,"partial_total":partial_total,
            "complete_evaluators":len(complete),"complete":final,"status":"Final" if final else ("Parcial" if complete else "Aguardando notas"),
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
        if length>5*1024*1024: raise ValueError("Requisição muito grande (limite de 5 MB).")
        return json.loads(self.rfile.read(length).decode("utf-8"))
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/api/export-xlsx":
            try:
                import openpyxl
                with dbconn() as con:
                    t=con.execute("SELECT filename,content FROM import_template WHERE id=1").fetchone()
                    candidates=con.execute("SELECT id,category,name,artistic_name,mannequin,shoe_size,body_size FROM candidates").fetchall()
                    score_rows=con.execute("SELECT candidate_id,communication,samba FROM scores").fetchall()
                if not t or not t["content"]: return self.send_json({"ok":False,"error":"Importe primeiro a planilha oficial para habilitar a exportação."},404)
                wb=openpyxl.load_workbook(io.BytesIO(t["content"]))
                ws=wb[wb.sheetnames[0]]
                lookup={(str(r['category']).strip().casefold(),str(r['name']).strip().casefold()):r for r in candidates}
                totals={}
                for r in score_rows:
                    # Contabiliza somente avaliações com os dois critérios preenchidos.
                    if r['communication'] is not None and r['samba'] is not None:
                        item=totals.setdefault(r['candidate_id'],{'total':0.0,'samba':0.0,'count':0})
                        item['total'] += float(r['communication']) + float(r['samba'])
                        item['samba'] += float(r['samba'])
                        item['count'] += 1
                # Colunas N e O: posição atual por categoria e total acumulado de pontos.
                ws.cell(1,14).value='Posição atual'
                ws.cell(1,15).value='Total de notas até o momento'
                # Calcula classificação parcial separadamente dentro de cada categoria.
                ranked_by_category={}
                for c in candidates:
                    score=totals.get(c['id'],{'total':0.0,'samba':0.0,'count':0})
                    if score['count']:
                        ranked_by_category.setdefault(str(c['category']).strip().casefold(),[]).append(
                            (c['id'],score['total'],score['samba'],str(c['name']).casefold()))
                positions={}
                for cat,items in ranked_by_category.items():
                    items.sort(key=lambda item:(-item[1],-item[2],item[3]))
                    for pos,item in enumerate(items,1):
                        positions[item[0]]=pos
                # Preenche também linhas com nome mesmo quando ainda não há notas ou chegada.
                for row in range(2,ws.max_row+1):
                    cat=ws.cell(row,1).value; name=ws.cell(row,2).value
                    key=(str(cat or '').strip().casefold(),str(name or '').strip().casefold())
                    c=lookup.get(key)
                    if c:
                        for col,val in ((3,c['artistic_name']),(4,c['mannequin']),(5,c['shoe_size']),(6,c['body_size'])):
                            ws.cell(row,col).value=val or None
                        score=totals.get(c['id'],{'total':0.0,'samba':0.0,'count':0})
                        ws.cell(row,14).value=positions.get(c['id'])
                        ws.cell(row,15).value=round(score['total'],1) if score['count'] else 0
                    else:
                        ws.cell(row,14).value=None
                        ws.cell(row,15).value=0
                out=io.BytesIO();wb.save(out);payload=out.getvalue()
                filename='LISTA_CANDIDATOS_ATUALIZADA.xlsx'
                self.send_response(200);self.send_header('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');self.send_header('Content-Disposition',f'attachment; filename="{filename}"');self.send_header('Content-Length',str(len(payload)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(payload);return
            except ImportError:return self.send_json({"ok":False,"error":"Falta openpyxl. Execute python -m pip install openpyxl"},500)
            except Exception as e:return self.send_json({"ok":False,"error":"Erro ao exportar Excel: "+str(e)},500)
        if path=="/api/active-stage":
            with dbconn() as con:
                row=con.execute("SELECT stage FROM active_stage WHERE id=1").fetchone()
            return self.send_json({"ok":True,"stage":row["stage"] if row else "AUDICAO"})
        if path=="/api/health": return self.send_json({"ok":True,"service":"UESP 2027","database":DB.name})
        if path=="/api/candidates":
            with dbconn() as con:
                rows=con.execute("SELECT id,category,name,artistic_name,mannequin,shoe_size,body_size,photo FROM candidates ORDER BY category,name COLLATE NOCASE").fetchall()
            return self.send_json({"ok":True,"candidates":[dict(x) for x in rows]})
        if path=="/api/arrival-candidates":
            # Controle de chegada: somente participantes classificados e publicados na audição.
            with dbconn() as con:
                rows=con.execute("""
                    SELECT c.id,c.category,c.name,c.artistic_name,c.mannequin,c.shoe_size,c.body_size,c.photo,
                           p.position AS audition_position
                    FROM candidates c
                    INNER JOIN audition_result_publication p ON p.candidate_id=c.id
                    ORDER BY CASE
                      WHEN lower(c.category) LIKE '%rainha%' THEN 1
                      WHEN lower(c.category) LIKE '%passista%' THEN 2
                      WHEN lower(c.category) LIKE '%diva%' THEN 3 ELSE 4 END,
                      p.position, c.name COLLATE NOCASE
                """).fetchall()
            return self.send_json({"ok":True,"candidates":[dict(x) for x in rows]})
        if path=="/api/arrivals":
            with dbconn() as con:
                rows=con.execute("SELECT candidate_id,arrived_at,device,status FROM arrivals").fetchall()
            return self.send_json({"ok":True,"arrivals":{str(r["candidate_id"]):{"arrived_at":r["arrived_at"],"device":r["device"],"status":r["status"] or "PRESENTE"} for r in rows}})
        if path=="/api/scores":
            with dbconn() as con:
                rows=con.execute("SELECT candidate_id,evaluator,communication,samba,updated_at FROM scores").fetchall()
            scores={}
            for r in rows: scores.setdefault(str(r["candidate_id"]),{})[r["evaluator"]]={"communication":r["communication"],"samba":r["samba"],"updated_at":r["updated_at"]}
            return self.send_json({"ok":True,"scores":scores})
        if path=="/api/evaluators":
            stage=urlparse(self.path).query.split('stage=')[-1].split('&')[0].upper() if 'stage=' in self.path else 'AUDICAO'
            if stage not in ('AUDICAO','CONCURSO'): stage='AUDICAO'
            with dbconn() as con:
                rows=con.execute("SELECT evaluator FROM evaluator_roster WHERE stage=? ORDER BY position,evaluator COLLATE NOCASE",(stage,)).fetchall()
            return self.send_json({"ok":True,"stage":stage,"evaluators":[r['evaluator'] for r in rows]})
        if path=="/api/contest-candidates":
            with dbconn() as con:
                candidates=contest_eligible(con)
                rs=con.execute("SELECT candidate_id,evaluator,aesthetics_sympathy,communication_elegance,samba,updated_at FROM contest_scores").fetchall()
            saved={}
            for r in rs:
                saved.setdefault(str(r["candidate_id"]),{})[r["evaluator"]]={"aesthetics_sympathy":r["aesthetics_sympathy"],"communication_elegance":r["communication_elegance"],"samba":r["samba"],"updated_at":r["updated_at"]}
            return self.send_json({"ok":True,"candidates":candidates,"scores":saved})
        if path=="/api/contest-report-data":
            # Relatório acompanha todos os participantes com chegada confirmada,
            # sem aplicar quotas/classificação da etapa anterior.
            with dbconn() as con:
                candidates=con.execute("""
                    SELECT c.id,c.category,c.name,c.artistic_name,c.photo,a.arrived_at,a.status
                    FROM candidates c JOIN arrivals a ON a.candidate_id=c.id
                    WHERE COALESCE(a.status,'PRESENTE')='PRESENTE'
                    ORDER BY c.category,c.name COLLATE NOCASE
                """).fetchall()
                rs=con.execute("SELECT candidate_id,evaluator,aesthetics_sympathy,communication_elegance,samba,updated_at FROM contest_scores").fetchall()
            saved={}
            for r in rs:
                saved.setdefault(str(r["candidate_id"]),{})[r["evaluator"]]={"aesthetics_sympathy":r["aesthetics_sympathy"],"communication_elegance":r["communication_elegance"],"samba":r["samba"],"updated_at":r["updated_at"]}
            return self.send_json({"ok":True,"candidates":[dict(x) for x in candidates],"scores":saved})
        if path=="/api/contest-results":
            with dbconn() as con:
                results=contest_result_rows(con)
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
            try:
                data=self.read_json()
            except Exception as e:
                return self.send_json({"ok":False,"error":"Dados inválidos: "+str(e)},400)
            stage=str(data.get("stage","")).strip().upper()
            if stage not in ("AUDICAO","CONCURSO"):
                return self.send_json({"ok":False,"error":"Etapa inválida. Use AUDICAO ou CONCURSO."},400)
            with dbconn() as con:
                con.execute("UPDATE active_stage SET stage=? WHERE id=1",(stage,))
                con.commit()
            return self.send_json({"ok":True,"stage":stage})
        if path=="/api/evaluators":
            try: data=self.read_json()
            except Exception as e: return self.send_json({"ok":False,"error":"Dados inválidos: "+str(e)},400)
            stage=str(data.get('stage','')).upper(); names=data.get('evaluators',[])
            if stage not in ('AUDICAO','CONCURSO') or not isinstance(names,list) or len(names)>30:
                return self.send_json({"ok":False,"error":"Etapa ou lista de avaliadores inválida."},400)
            clean=[]
            for name in names:
                name=str(name).strip()
                if name and len(name)<=80 and name.casefold() not in [x.casefold() for x in clean]: clean.append(name)
            with dbconn() as con:
                con.execute("DELETE FROM evaluator_roster WHERE stage=?",(stage,))
                con.executemany("INSERT INTO evaluator_roster(stage,evaluator,position) VALUES(?,?,?)",[(stage,n,i) for i,n in enumerate(clean,1)])
                con.commit()
            return self.send_json({"ok":True,"stage":stage,"evaluators":clean})
        if path=="/api/clear-contest-scores":
            try: data=self.read_json()
            except Exception as e: return self.send_json({"ok":False,"error":"Dados inválidos: "+str(e)},400)
            if not data.get('confirm'): return self.send_json({"ok":False,"error":"Confirme a limpeza da etapa Concurso."},400)
            with dbconn() as con:
                count=con.execute("SELECT COUNT(*) FROM contest_scores").fetchone()[0]
                roster=con.execute("SELECT COUNT(*) FROM evaluator_roster WHERE stage='CONCURSO'").fetchone()[0]
                con.execute("DELETE FROM contest_scores")
                con.execute("DELETE FROM evaluator_roster WHERE stage='CONCURSO'")
                con.commit()
            return self.send_json({"ok":True,"deleted":count,"deleted_evaluators":roster,"message":"Notas e cadastro de avaliadores do Concurso apagados; Audição e cadastro de participantes preservados."})
        if path=="/api/clear-scores":
            # Reinicia os dados operacionais como numa nova importação, mas preserva
            # a lista oficial de candidatos e a planilha-modelo importada.
            try:
                data=self.read_json()
                if not data.get('confirm'):
                    return self.send_json({"ok":False,"error":"Confirme a limpeza completa antes de continuar."},400)
                with dbconn() as con:
                    deleted_scores=con.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
                    deleted_contest=con.execute("SELECT COUNT(*) FROM contest_scores").fetchone()[0]
                    deleted_arrivals=con.execute("SELECT COUNT(*) FROM arrivals").fetchone()[0]
                    deleted_photos=con.execute("SELECT COUNT(*) FROM candidates WHERE COALESCE(photo,'')<>''").fetchone()[0]
                    con.execute("DELETE FROM scores")
                    con.execute("DELETE FROM contest_scores")
                    con.execute("DELETE FROM evaluator_roster")
                    con.execute("DELETE FROM audition_result_publication")
                    con.execute("DELETE FROM arrivals")
                    con.execute("UPDATE candidates SET artistic_name='',mannequin='',shoe_size='',body_size='',photo=''")
                    con.commit()
                return self.send_json({"ok":True,"deleted":deleted_scores,"deleted_contest":deleted_contest,
                    "deleted_arrivals":deleted_arrivals,"deleted_photos":deleted_photos,
                    "message":"Sistema reiniciado: notas, avaliadores, dados da chegada, fotos e publicações foram apagados. A lista oficial de candidatos e a planilha importada foram preservadas."})
            except Exception as e:
                return self.send_json({"ok":False,"error":"Não foi possível limpar os dados: "+str(e)},500)
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
                return self.send_json({"ok":True,"published":len(published),"published_at":now,"message":"Resultado da Audição disponibilizado para avaliação do Concurso."})
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
            with dbconn() as con:
                eligible={x["id"]:x for x in contest_eligible(con)}
                if cid not in eligible: return self.send_json({"ok":False,"error":"Participante não está entre os classificados para o concurso. Confira se a audição está completa e a chegada registrada."},400)
                current=con.execute("SELECT aesthetics_sympathy,communication_elegance,samba FROM contest_scores WHERE candidate_id=? AND evaluator=?",(cid,evaluator)).fetchone()
                oldvals={k:(current[k] if current else None) for k in ("aesthetics_sympathy","communication_elegance","samba")}
                submitted={"aesthetics_sympathy":data.get("aesthetics_sympathy"),"communication_elegance":data.get("communication_elegance"),"samba":data.get("samba")}
                for key,value in submitted.items():
                    if key in data: oldvals[key]=float(value) if value not in (None,"") else None
                if eligible[cid]["category"].strip().casefold()=="passista de ouro": oldvals["aesthetics_sympathy"]=None
                now=datetime.now().astimezone().isoformat(timespec="seconds")
                con.execute("""INSERT INTO contest_scores(candidate_id,evaluator,aesthetics_sympathy,communication_elegance,samba,updated_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(candidate_id,evaluator) DO UPDATE SET
                    aesthetics_sympathy=excluded.aesthetics_sympathy,communication_elegance=excluded.communication_elegance,
                    samba=excluded.samba,updated_at=excluded.updated_at""",(cid,evaluator,oldvals["aesthetics_sympathy"],oldvals["communication_elegance"],oldvals["samba"],now));con.commit()
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
            artistic_name=str(data.get("artistic_name", "")).strip()
            mannequin=str(data.get("mannequin", "")).strip()
            shoe_size=str(data.get("shoe_size", "")).strip()
            body_size=str(data.get("body_size", "")).strip()
            photo=data.get("photo", None)
            if photo is not None and (not isinstance(photo,str) or len(photo)>3_500_000 or not photo.startswith("data:image/")):
                return self.send_json({"ok":False,"error":"Foto inválida ou muito grande. Tire outra foto."},400)
            with dbconn() as con:
                exists=con.execute("SELECT id FROM candidates WHERE id=?",(cid,)).fetchone()
                if not exists:return self.send_json({"ok":False,"error":"Candidato não encontrado."},404)
                if photo is None:
                    con.execute("UPDATE candidates SET artistic_name=?,mannequin=?,shoe_size=?,body_size=? WHERE id=?",(artistic_name,mannequin,shoe_size,body_size,cid))
                else:
                    con.execute("UPDATE candidates SET artistic_name=?,mannequin=?,shoe_size=?,body_size=?,photo=? WHERE id=?",(artistic_name,mannequin,shoe_size,body_size,photo,cid))
                if present or absent:
                    now=datetime.now().astimezone().isoformat(timespec="seconds")
                    status="AUSENTE" if absent else "PRESENTE"
                    con.execute("INSERT INTO arrivals(candidate_id,arrived_at,device,status) VALUES(?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET arrived_at=excluded.arrived_at,device=excluded.device,status=excluded.status",(cid,now,data.get("device","Recepção"),status))
                    row=con.execute("SELECT candidate_id,arrived_at,device,status FROM arrivals WHERE candidate_id=?",(cid,)).fetchone()
                    con.commit()
                    return self.send_json({"ok":True,"arrival":{"arrived_at":row["arrived_at"],"device":row["device"],"status":row["status"]}})
                if data.get("save_only"):
                    row=con.execute("SELECT candidate_id,arrived_at,device,status FROM arrivals WHERE candidate_id=?",(cid,)).fetchone()
                    con.commit()
                    return self.send_json({"ok":True,"arrival":{"arrived_at":row["arrived_at"],"device":row["device"],"status":row["status"]} if row else None})
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
                artistic=str(row[2]).strip() if len(row)>2 and row[2] is not None else ""
                mannequin=str(row[3]).strip() if len(row)>3 and row[3] is not None else ""
                shoe=str(row[4]).strip() if len(row)>4 and row[4] is not None else ""
                body=str(row[5]).strip() if len(row)>5 and row[5] is not None else ""
                rows.append((cat,name,artistic,mannequin,shoe,body))
            wb.close()
            if not rows:return self.send_json({"ok":False,"error":"Nenhum candidato válido nas colunas A e B."},400)
            with dbconn() as con:
                # Atualiza os dados importados sem apagar notas, chegadas ou informações já salvas.
                for cat,name,artistic,mannequin,shoe,body in rows:
                    con.execute("INSERT OR IGNORE INTO candidates(category,name) VALUES (?,?)",(cat,name))
                    con.execute("UPDATE candidates SET artistic_name=CASE WHEN ?<>'' THEN ? ELSE artistic_name END, mannequin=CASE WHEN ?<>'' THEN ? ELSE mannequin END, shoe_size=CASE WHEN ?<>'' THEN ? ELSE shoe_size END, body_size=CASE WHEN ?<>'' THEN ? ELSE body_size END WHERE category=? AND name=?",(artistic,artistic,mannequin,mannequin,shoe,shoe,body,body,cat,name))
                con.execute("INSERT INTO import_template(id,filename,content) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET filename=excluded.filename,content=excluded.content",(filename,sqlite3.Binary(raw)))
                con.commit()
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

if __name__=="__main__":
    init_db()
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