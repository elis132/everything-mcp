<div align="center">
  <h1>⚡ Everything MCP</h1>
  <p>
    <strong><a href="https://www.voidtools.com/ko-kr/">voidtools Everything</a>용 MCP 서버 — AI 에이전트가 윈도우의 수백만 개 파일을 밀리초 단위로 검색합니다.</strong>
  </p>
  <p><a href="README.md">English</a> · <b>한국어</b></p>
</div>

---

## 이 포크에서 달라진 점 (네이티브 IPC)

원본([elis132/everything-mcp](https://github.com/elis132/everything-mcp))은 매 검색마다 `es.exe`를 실행하고, 결과 파일마다 `os.stat()`으로 크기·날짜를 다시 읽습니다. 로컬 디스크에서는 문제가 없지만, **NAS·공유폴더(SMB)·RaiDrive 같은 네트워크 드라이브**에서는 결과 1건마다 네트워크 왕복이 생겨 수십 초가 걸리거나 시간 초과가 납니다.

이 포크는 Everything이 공식 문서로 제공하는 **IPC(`WM_COPYDATA` / `QUERY2`, es.exe가 내부적으로 쓰는 것과 같은 프로토콜)** 로 직접 통신합니다.

- **es.exe 불필요** — 순수 Python `ctypes`. SDK DLL·추가 패키지 없음.
- **크기·날짜가 Everything 응답에 들어 있음** — 파일별 `os.stat()`이 사라져 네트워크 드라이브에서 특히 빠름.
- **자동 선택** — Everything이 실행 중이면 IPC, 아니면 기존 es.exe 방식으로 자동 전환. 도구·쿼리 문법·안전장치(#18 프리즈 방지, 세션 간 잠금)는 그대로.

### 실측 (같은 PC·같은 서버, 전송 방식만 다름)

Everything 1.4.1.1032 x64, 색인 약 610만 항목(로컬 + NAS 폴더 색인 포함):

| 호출 | es.exe (원본) | IPC (이 포크) | 배수 |
|---|---:|---:|---:|
| 한글 이름 검색 `반사율` | 4,314 ms | 646 ms | 6.7× |
| NAS 공유폴더 xlsx 100건 | 6,986 ms | 63 ms | 111× |
| NAS 이미지 500건 | 49,479 ms | 250 ms | 198× |
| `everything_find_recent` (오늘) | 1,175 ms | 149 ms | 7.9× |
| `everything_count_stats` | 1,238 ms | 89 ms | 14× |

---

## 빠른 시작

### Claude Code

```
/plugin marketplace add yazzang-homelab/everything-mcp
/plugin install everything-mcp@everything-mcp
```

### 다른 MCP 클라이언트 (Codex, Gemini CLI, Cursor 등)

이 포크를 소스에서 실행합니다(PyPI의 `everything-mcp`는 원본 패키지입니다):

```bash
git clone https://github.com/yazzang-homelab/everything-mcp.git
cd everything-mcp
pip install -e .
```

```json
{
  "mcpServers": {
    "everything": {
      "command": "python",
      "args": ["-m", "everything_mcp"]
    }
  }
}
```

| 클라이언트 | 등록 방법 |
|---|---|
| **Claude Code** | 위 플러그인, 또는 `claude mcp add everything -- python -m everything_mcp` |
| **Claude Desktop** | `%APPDATA%\Claude\claude_desktop_config.json`에 위 JSON 추가 |
| **Codex CLI** | `codex mcp add everything -- python -m everything_mcp` |
| **Gemini CLI** | `gemini mcp add -s user everything python -m everything_mcp` |
| **Cursor / Windsurf** | MCP 설정에 위 JSON 붙여넣기 |

---

## 준비물

1. **Windows** + [Everything](https://www.voidtools.com/ko-kr/) 설치 후 **실행 중**(트레이 아이콘)
   - 1.4.1.1032 이상 권장. 대용량 색인(수백만 항목·NAS 포함)이면 **64비트 판**을 쓰세요 — 32비트 판은 2GB 메모리 한계로 멈출 수 있습니다.
2. **Python 3.10+**
3. es.exe는 **선택 사항**입니다. 아래 경우에만 필요합니다.
   - `everything_count_stats`의 **총 용량**(IPC에는 용량 합계 기능이 없음)
   - `EVERYTHING_TRANSPORT=es`로 원본 방식을 강제할 때

---

## 환경 변수 (모두 선택)

| 변수 | 설명 | 예 |
|---|---|---|
| `EVERYTHING_TRANSPORT` | `ipc` 또는 `es` 강제 (기본: 자동) | `ipc` |
| `EVERYTHING_INSTANCE` | 이름 붙은 인스턴스(설정한 경우만) | `1.5a` |
| `EVERYTHING_ES_PATH` | es.exe 경로 | `C:\Program Files\Everything\es.exe` |
| `EVERYTHING_MAX_RESULTS_CAP` | 검색당 최대 결과 수 (기본 1000) | `200` |

> `EVERYTHING_INSTANCE`는 Everything에서 직접 이름 붙은 인스턴스를 만든 경우에만 설정하세요(도구 → 옵션 → 일반 → 인스턴스). 대부분은 **기본 인스턴스**라 설정하면 오히려 연결이 끊깁니다.

---

## 도구 (5개)

| 도구 | 용도 | 예시 |
|---|---|---|
| `everything_search` | 기본 검색 | `ext:xlsx 런시트`, `path:"\\NAS\공유" 반사율` |
| `everything_search_by_type` | 종류별(문서·이미지·코드 등) | `file_type="document", query="회의록"` |
| `everything_find_recent` | 최근 수정 파일 | `period="today", extensions="pptx"` |
| `everything_file_details` | 메타데이터·텍스트 미리보기 | `paths=["C:\\...\\config.yaml"], preview_lines=30` |
| `everything_count_stats` | 건수·용량·확장자별 통계 | `query="ext:log", breakdown_by_extension=true` |

### 자주 쓰는 검색 문법

```
런시트 ext:xlsx                    이름에 '런시트' + 엑셀
path:"\\192.168.0.141\공유폴더" 보고서   특정 공유폴더 아래
ext:pdf;docx;hwp                  여러 확장자 (한글 hwp 포함)
dm:today / dm:last7days           오늘 / 최근 7일 수정
size:>100mb                       100MB 초과
"정확한 파일명.txt"                 정확히 일치
보고서 | 회의록                     OR
!Old                              제외
parent:"C:\프로젝트\src"            폴더 바로 아래 항목만
```

### 한국어 사용 팁

- **한글 부분 일치가 됩니다** — `측정`으로 `반사율 측정 결과`를 찾습니다(Everything은 이름 부분 문자열 매칭).
- **띄어쓰기는 AND** — `반사율 측정 결과 폴더`처럼 실제 이름에 없는 단어를 붙이면 0건이 됩니다. 기억나는 고유 단어 1~2개로 검색하세요.
- **네트워크 드라이브**(NAS·RaiDrive·매핑 드라이브)는 Everything의 `도구 → 옵션 → 색인 → 폴더`에 추가해야 검색됩니다. 이때 **변경 감시(Monitor changes)는 끄고 예약 재스캔**을 쓰세요 — 네트워크 폴더 감시는 버퍼가 넘쳐 전체 재스캔과 멈춤의 원인이 됩니다.
- C: 같은 NTFS 드라이브를 **폴더 색인에 또 추가하지 마세요** — NTFS 색인과 이중으로 잡혀 메모리가 약 2배가 됩니다.

---

## 느린 함수 주의 (원본과 동일)

`content:`(파일 내용), `width:`/`height:`, 음악 태그, 색인되지 않은 `dc:`/`da:`/`attrib:`는 Everything이 **후보 파일을 디스크에서 하나씩 읽게** 만들고, 그동안 다른 검색에 응답하지 않습니다. 서버는 이런 조건을 마지막에 실행하고, 후보가 많으면(내용 검색 1,000개·100MB 초과 등) 거절하며 후보 수를 알려줍니다. `path:`·`ext:`·이름으로 범위를 먼저 좁히세요.

같은 이유로 IPC 전송은 **Everything이 색인한 속성만** 응답으로 요청합니다. 생성일(date created)은 기본적으로 색인되지 않으므로, 필요하면 `도구 → 옵션 → 색인 → 생성일 색인`을 켜세요(켜면 결과에 자동 포함).

---

## 문제 해결

| 증상 | 확인 |
|---|---|
| `IPC window not found` | Everything이 실행 중인지(트레이), 이름 붙은 인스턴스면 `EVERYTHING_INSTANCE` |
| 항상 `busy` | 이전 느린 검색이 끝나길 기다리거나 Everything 재시작 |
| 네트워크 폴더 결과가 오래됨 | 폴더 색인의 예약 재스캔 시각 확인, 필요 시 Everything에서 수동 재스캔 |
| 총 용량이 `unavailable` | es.exe 설치(`winget install voidtools.Everything.Cli`) 또는 그대로 사용(건수는 정상) |

---

## 개발

```bash
pip install -e ".[dev]"
pytest            # Everything이 실행 중이면 실제 IPC 왕복 테스트도 함께 수행
ruff check src/ tests/
```

## 라이선스

MIT — 원본 저작권 elis132. 이 포크의 IPC 전송 추가분도 같은 MIT 라이선스입니다.
