<h1 align="center">DAON-VMate</h1>

<p align="center">당신만의 Virtual Mate</p>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-1fa669?style=flat-square" alt="License MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/PySide6-Qt-41CD52?style=flat-square&logo=qt&logoColor=white" alt="PySide6">
  <img src="https://img.shields.io/badge/Live2D-Cubism-2C2C2C?style=flat-square" alt="Live2D">
  <img src="https://img.shields.io/badge/MCP-stdio-orange?style=flat-square" alt="MCP">
</p>


> [!NOTE]
>
> 현재 이 프로그램은 **개발 중(알파)** 입니다.
>
> 아직 부족하거나 개발이 덜 된 부분들이 많습니다.
>
> 동작과 UI는 변경될 수 있습니다. 버그 및 개선 사항은 Issues탭을 통해 알려 주세요. 감사합니다.
>

---

## DAON-VMate란?

**DAON-VMate**는 **Live2D 캐릭터**와 **대화형 LLM**을 한 데 올린 **Windows 데스크톱용 Virtual Mate** GUI형태의 프로그램 입니다. 내 바탕화면 안에서 캐릭터가 보여지며, 서로 대화 할 수 있고 화면을 공유하여 같이 이야기를 나눌 수 있으며 이 **캐릭터가 내 컴퓨터 안에 머무는** 듯한 느낌을 줍니다.

당신의 캐릭터는 **시스템 프롬프트** 설정에 따라 달라집니다. 당신의 친한 친구부터, 당신을 보조하는 비서 까지, 원하는 역할에 맞게 시스템 프롬프트를 조절해 사용 가능합니다.

현재는 이미지·텍스트·PDF 첨부, 현재 화면에 대한 화면공유 기능과, MCP를 통한 웹 검색을 사용 할 수 있습니다.

추후, 업무 보조·일상적인 잡무까지 도울 수 있도록 기능과 안정성을 넓혀 갈 계획입니다.

---

## 현재 구현 사항

### LLM

- **Ollama** — 로컬 `/api/chat` 연동, 스트리밍 응답 지원  
- **OpenAI 호환 API** — Chat Completions 형식 엔드포인트

### TTS

- **Edge TTS** — Microsoft Edge 음성 엔진 기반(`edge-tts`)
- **Eleven Labs TTS** — Eleven Labs API 연동
- **GPT-SoVITS** — HTTP API로 연동되는 음성 합성 백엔드  
- **OpenAI TTS** — OpenAI Speech API 또는 호환 엔드포인트

### 현재 개발된 Default MCP (Model Context Protocol)

- **웹 검색** — `mcp_extension`에 포함된 예시 MCP 서버(DuckDuckGo 등)로 검색 도구 연동 가능
- **파일 조작** - `mcp_extension`에 포함된 예시 workspace내의 파일 조작

## MCP Server Extension

MCP(Model Context Protocol) **stdio 서버**는 **`mcp_extension/`을 확장 모듈 영역**으로 두었습니다. 서버마다 `servers/<이름>/` 폴더로 나누어 코드와 설정 조각을 함께 두면, 저장소를 크게 뜯지 않고도 **도구를 추가·교체 관리**하기 쉬워 이 같은 방식을 사용하였습니다.

### 설정이 합쳐지는 방식

1. **기준 파일** — `config/settings.yaml`의 `mcp.config_file`이 가리키는 JSON(기본: `mcp_extension/mcp_servers.json`)에 전역 서버 목록을 둡니다.  
2. **프래그먼트 병합** — `mcp_extension/servers/<서버이름>/mcp_servers.fragment.json`을 **폴더 이름 알파벳 순**으로 읽어 합칩니다. **같은 서버 이름(키)이면 뒤쪽(프래그먼트)이 앞을 덮어씁니다.**  
3. **키 이름** — 루트 객체는 `mcp_servers` 또는 `mcpServers` 둘 다 인식합니다.

기동 시 이 규칙으로 만든 서버 맵이 MCP 클라이언트에 넘어가며, 설정에서 MCP를 켠 경우 **채팅 LLM의 도구 호출 루프**와 연결됩니다.

### 디렉터리 예시

| 경로 | 역할 |
|------|------|
| `mcp_extension/mcp_servers.json` | 통합 MCP 서버 정의(기본 진입점) |
| `mcp_extension/servers/web_search/` | 예: 웹 검색 stdio 서버 + `mcp_servers.fragment.json` |
| `mcp_extension/servers/echo_example/` | 예: 동작 확인용 에코 서버 |

새 도구는 `servers/<원하는이름>/`에 실행 엔트리를 두고, fragment에 `command` / `args` / `cwd` 등을 정의하면 됩니다.

### UX/UI
- 편의성을 고려하여 최대한 유저가 손보는 구간을 줄이는 것을 목표 하였습니다.
- 간단한 화면구성을 통해 최대한 유저가 간단하게 접근하고 사용가능하도록 제작하였습니다.

이후에도 연동할 수 있는 LLM 종류와 도구, UX/UI를 꾸준히 추가/보강할 계획 입니다.

## 시작하기
### 요구 사항

| 항목 | 내용 |
|------|------|
| OS | **Windows 권장** (화면 공유·창 목록 등 Win32 API 사용) |
| Python | 3.10 이상 권장 |
| 그래픽 | Live2D 렌더링을 위한 **OpenGL** 지원 환경 |

### Windows: `run.bat`

[uv](https://github.com/astral-sh/uv)가 PATH에 있으면 `.venv` 생성·`pip install -r requirements.txt` 후 `main.py` 실행까지 자동입니다. 이미 `.venv`가 있으면 설치를 건너뜁니다.

```bat
run.bat
```

### 수동 실행

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
python main.py
```

---

## 설정

| 구간 | 파일 / UI | 설명 |
|------|-----------|------|
| 전체 | `config/settings.yaml` | 앱 **환경 설정** 메뉴에서 저장하거나 직접 편집 |
| LLM | 동일 | `provider`, 모델, URL, API 키, 스트리밍, MCP 도구 사용 여부 등 |
| TTS | 동일 | 제공자별 음성·엔드포인트 |
| Live2D | 동일 | 모델 폴더, 스케일, 감정 자동 반영 |
| MCP | 동일 | `enabled`, `config_file` (기본 `mcp_extension/mcp_servers.json`) |

**MCP 서버 추가**: `mcp_extension/servers/<이름>/`에 서버 코드를 두고, 같은 디렉터리의 `mcp_servers.fragment.json`으로 조각 설정을 병합할 수 있습니다.

---

## 제한 사항 · 호환성

- **API 기반 LLM**: 서버가 Chat Completions의 **`file`** 콘텐츠 파트를 지원해야 합니다. 미지원 시 요청이 거절될 수 있습니다.
- **Ollama**: 바이너리 전송 대신 **추출된 텍스트**만 프롬프트에 포함됩니다. 스캔 PDF 등은 추출이 비어 있을 수 있습니다.
- **Live2D**: Cubism SDK 및 **개별 모델 에셋**의 라이선스·상업 이용 조건은 각각 확인이 필요합니다.

---

## Default LIVE2D 모델
  - [majyo](https://booth.pm/en/items/6499774)
  - [Alexia](https://booth.pm/en/items/5576188)
  
---

## 라이선스
이 레포지토리의 아이디어와 로직은 오픈소스인 [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber)에서 참고하여 제작하였습니다.

이에 따라 이 코드 또한 [MIT License](./LICENSE)를 추종합니다.

---

<p align="center">
  <sub>DAON-VMate</sub>
</p>
