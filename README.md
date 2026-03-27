# RPview

GIF, PNG, JPG, MP4 파일을 캔버스에 자유롭게 배치해 참고 이미지/영상으로 활용하는 레퍼런스 뷰어입니다.

![GIF](https://github.com/user-attachments/assets/63b03d8c-1a59-4b61-9518-eadad705a92c)

<br>

## 설치

1. `Setup.bat` 실행
2. 설치 창이 열리며 Python 환경이 자동으로 구성됩니다 (인터넷 연결 필요, 약 1~2분 소요)
3. 설치 완료 후 폴더 내 `RPview` 바로가기로 실행

> 설치는 최초 1회만 필요합니다.
> 이후에는 `RPview` 바로가기만 사용하면 됩니다.

---

## 업데이트

앱 업데이트 시 Python 재설치 없이 **스크립트 파일만 교체**하면 됩니다.

1. `_app` 폴더 안의 `gif_ref_viewer.py` 를 새 버전으로 교체
2. `RPview` 바로가기로 실행

---

## 시스템 요구사항

- Windows 10 / 11
- 인터넷 연결 (최초 설치 시)
- 권장 RAM: 500MB 이상 (대용량 레퍼런스 작업 시 2GB+)

---

## 파일 구조 (설치 후)

```
RPview.lnk          ← 실행 바로가기
_app/
  gif_ref_viewer.py ← 앱 본체 (업데이트 시 이 파일 교체)
  RPview.bat
  python/           ← 임베디드 Python 환경
```

<br>

## 단축키

| 키 | 기능 |
|---|---|
| `Space` | 재생 / 정지 |
| `← / →` | 이전 / 다음 프레임 |
| `L` | 레이어 패널 열기/닫기 |
| `H` | 전체 보기 |
| `Z` | 줌 초기화 |
| `T` | 항상 위 토글 |
| `Delete` | 선택 삭제 |
| `Esc` | 선택 해제 |
| `Ctrl+Z` | 실행 취소 |
| `Ctrl+Shift+Z` | 다시 실행 |
| `Ctrl+A` | 전체 선택 |
| `Ctrl+C` | 복사 |
| `Ctrl+D` | 복제 |
| `Ctrl+V` | 붙여넣기 |
| `Ctrl+G` | 그룹 추가 |
| `Ctrl+T` | 텍스트 추가 |
| `Ctrl+I` | 색상 반전 |
| `Ctrl+N` | 새 프로젝트 |
| `Ctrl+O` | 열기 |
| `Ctrl+S` | 저장 |
| `Ctrl+Shift+S` | 다른 이름으로 저장 |
