# RPview — CLAUDE.md

## 프로젝트 개요

**RPview** (ReView)는 GIF, PNG, JPG, MP4 등의 미디어 파일을 캔버스에 배치해 참고 이미지/영상으로 활용하는 레퍼런스 뷰어 앱입니다.

- 메인 소스: `gif_ref_viewer.py` (단일 파일)
- 실행: `python/python.exe gif_ref_viewer.py` (번들 Python 사용)
- 설치 경로 (구): `C:\Users\pjbug\Desktop\RPVW_install_v5`
- 번들 Python: `python/python.exe` (Python 3.11, 독립 환경)

---

## 주요 클래스 구조

| 클래스 | 역할 |
|---|---|
| `MainWindow` | 메인 윈도우, 단축키 처리, 상태 저장/복원 |
| `CanvasWidget` | 아이템 배치 캔버스, 마우스/줌/팬 처리 |
| `ImageItem` | PNG/JPG 정적 이미지 아이템 |
| `GifItem` | GIF 애니메이션 아이템 (QMovie 기반) |
| `VideoItem` | MP4/AVI/MOV 영상 아이템 (OpenCV 기반) |
| `_VideoDecodeWorker` | VideoItem의 디코딩 전담 워커 스레드 |
| `LayerPanel` | 레이어 패널 (오버레이) |
| `_ZoomHUD` | 좌하단 줌% + RAM 사용량 표시 위젯 |
| `ShortcutOverlay` | Tab 홀드 시 단축키 안내 오버레이 |
| `GifControlBar` | GIF/MP4 재생 컨트롤 바 |
| `_ItemBlendBar` | 아이템 호버 시 블렌드모드/불투명도 패널 |
| `_ItemHoverBar` | 아이템 호버 시 정보/스크럽 바 |

---

## 상태 저장

- 파일: `review_config.json` (앱과 같은 디렉토리)
- 2초마다 자동 저장 (`save_timer`)
- 종료 시 `atexit`으로 저장
- 주요 저장 항목: 윈도우 크기/위치, 패널 표시 여부, 탭 목록, 각 아이템 상태

---

## 의존성

```
PyQt5
opencv-python  (HAS_OPENCV — MP4 재생)
psutil         (RAM 사용량 표시 — 없으면 표시 안 됨)
```

번들 Python에 설치:
```
python/python.exe -m pip install psutil
```

---

## 단축키

### 기본
| 키 | 기능 |
|---|---|
| `Space` | 재생 / 정지 (GIF·MP4) |
| `← / →` | 이전 / 다음 프레임 (GIF·MP4 선택 시) |
| `L` | 레이어 패널 열기/닫기 |
| `H` | 전체 보기 |
| `Z` | 줌 초기화 |
| `T` | 항상 위 토글 |
| `Delete` | 선택 삭제 |
| `Esc` | 선택 해제 |

### Ctrl 조합
| 키 | 기능 |
|---|---|
| `Ctrl+A` | 전체 선택 |
| `Ctrl+C` | 복사 |
| `Ctrl+D` | 복제 |
| `Ctrl+V` | 붙여넣기 |
| `Ctrl+Z` | 실행 취소 |
| `Ctrl+Shift+Z` | 다시 실행 |
| `Ctrl+G` | 그룹 추가 |
| `Ctrl+T` | 텍스트 추가 |
| `Ctrl+I` | 색상 반전 |
| `Ctrl+N` | 새 프로젝트 |
| `Ctrl+O` | 열기 |
| `Ctrl+S` | 저장 |
| `Ctrl+Shift+S` | 다른 이름으로 저장 |

---

## 최근 작업 내역 (v14.1 기준)

### MP4 재생 개선
- `_VideoDecodeWorker.seek_to()`: 후진 seek 시 `_reopen_cap()` 호출 (FFmpeg 상태 오염 방지), 실패 시 재시도 로직 추가
- `_cleaning_up` 플래그 추가 → cleanup 중 도착하는 seek 신호 무시 (크래시 방지)
- seek 방식을 debounce → **in-flight 게이트** 방식으로 교체
  - `_seek_in_flight` 플래그: 한 번에 1개 seek만 worker 큐에 전달
  - `_onFrameReady`에서 `was_in_flight` 체크 후 pending seek dispatch (일반 재생 중 오발 방지)
- `_step_repeat_timer` 간격: 1ms → 80ms (방향키 꾹 누름 시 극단 이동 방지)

### 방향키 프레임 이동
- `GifItem`, `VideoItem`에 `setFocusPolicy(Qt.ClickFocus)` 추가
- 두 클래스에 `keyPressEvent` / `keyReleaseEvent` 직접 구현
  - `←` / `→`: 1프레임 이동 (누르는 순간 즉시, 300ms 후 연속)
  - 선택된 모든 아이템에 동시 적용

### UI
- `_ZoomHUD`: 줌% 옆에 RAM 사용량 표시 (psutil, 2초 갱신)
- `_ItemBlendBar` ("Normal 100%"): 아이템 삭제 시 `cleanup()`에서 `deleteLater()` 처리 (삭제 후 잔상 버그 수정)
- `ShortcutOverlay`: `← / →  이전 / 다음 프레임` 항목 추가, 패널 높이 360 → 386
- 레이어 패널 기본값: 앱 종료 시 상태 그대로 복원 (생성 시 `hide()` 명시)

---

## 주의사항

- `_VideoDecodeWorker`는 별도 `QThread`에서 동작 — UI 스레드에서 직접 cap 접근 금지
- GIF seek: `QMovie.jumpToFrame()` (메모리 즉시) vs MP4 seek: OpenCV `cap.set()` (키프레임 디코딩 필요, 느림)
- `review_config.json`의 `panel_visible` 값이 패널 초기 상태를 결정
