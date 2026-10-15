# Kaitai Interactive SVG

Kaitai Struct의 `.ksy` 파일을 **독립 SVG**로 변환하는 Python 프로그램입니다.
기본 출력은 정적 SVG이며, `--interactive`로 문서 툴팁·연결선 강조·확대·이동
기능을 활성화합니다.

![SQLite3 스키마 SVG 예제](examples/sqlite3.svg)

*SQLite 예제. 생성 명령은 [빠른 시작](#빠른-시작)에 있습니다.*


## 빠른 시작

Python 3.11 이상이 필요합니다. 저장소에서 `uv`로 의존성을 설치하고, 포함된
SQLite 예제를 변환할 수 있습니다.

```sh
uv sync --locked
uv run kaitai-svg examples/sqlite3.ksy --interactive
```

SVG는 입력 `.ksy` 파일 옆에 같은 이름으로 저장됩니다. 위 명령은
`examples/sqlite3.svg`를 갱신합니다. 위 미리보기에는 추가로
`--color-type-edges`, `--router orthogonal`,
`--hide vlq_base128_be.ksy`를 사용했습니다. 자신의 스키마에는 파일 경로나
인용된 glob을 지정하세요.

```sh
uv run kaitai-svg path/to/schema.ksy
uv run kaitai-svg "schemas/**/*.ksy" -I ./format-library
```

`uv`를 사용하지 않는 경우 저장소에서 `python -m pip install .`을 실행한 뒤
`kaitai-svg` 명령을 사용할 수 있습니다. `python -m kaitai_svg`와
`kaitai-interactive-svg`도 같은 CLI입니다.

기본 정적 SVG에는 JavaScript, 조작 버튼, 사용자 지정 툴팁, Tab 포커스를 넣지
않습니다. 도면 크기를 `width`·`height`와 `viewBox`에 기록하며 글자 선택과
브라우저 기본 스크롤을 사용할 수 있습니다. 문서는 SVG의 `<title>`에 보존하므로
이를 지원하는 뷰어에서 기본 문서 툴팁을 볼 수 있습니다. `--interactive`는 아래
연결선 표시 옵션과 독립적으로 사용할 수 있습니다.

import와 중첩 정의 연결선은 기본적으로 그리지 않습니다. `--show-imports`로
주황색 import 선을, `--show-nested-types`로 부모와 중첩 타입·enum 사이의
청록색 선을 각각 표시합니다. 해당 범례도 함께 나타납니다. 두 옵션은 독립적으로
사용할 수 있으며, import 파일 검증·타입/enum 정의 포함·필드 참조선에는 영향을
주지 않습니다.

`--treeshake`를 지정하면 입력 파일에서 참조하지 않는 import 파일의 타입과
enum을 SVG에서 제외합니다. 타입·enum 참조와 표현식에 명시된 `::` 참조를
따라가므로 표시되는 정의의 참조 대상은 계속 표시하고, 중첩 정의가 쓰이면 그
부모 정의도 함께 남깁니다.
입력 파일에 선언된 정의는 모두 표시합니다. 이 옵션은 import 파일의 로딩과
검증에는 영향을 주지 않으며 `--show-imports`, `--show-nested-types`, `--hide`와
함께 사용할 수 있습니다.

`--hide <id>|<filename>`으로 타입·enum·필드·매개변수·인스턴스·enum 값을
렌더링에서 제외합니다. 옵션을 여러 번 지정하면 모든 대상을 함께 숨깁니다. ID는
`text_payload`, `packet::text_payload`, `packet::text_payload::text`처럼
지정하며, 짧은 ID는 일치하는 모든 요소에 적용됩니다. SVG의
`data-node-id`·`data-row-id`도 사용할 수 있습니다. `.ksy` 파일명을 지정하면 그
파일의 모든 정의를 숨깁니다. 파일명만 쓰면 같은 이름의 모든 파일에 적용되고,
디렉터리가 있는 경로로 특정 파일을 지정할 수 있습니다. 경로는 입력 스키마의
디렉터리 기준이며, 현재 작업 디렉터리에서 존재하는 경로나 절대 경로도
받습니다.

숨긴 요소에 붙은 연결선과 비어 있는 파일 그룹도 제외한 뒤 배치합니다. 타입을
숨겨도 그 안에서 선언한 중첩 타입은 별도 정의로 남으며, 숨긴 타입을 참조하는
다른 요소의 필드는 그대로 표시합니다. 일치하지 않는 대상은 무시하고, 숨긴
파일도 import 로딩·검증과 원래 위치·크기 계산에는 포함합니다.
`--interactive`와 다른 표시·라우터 옵션을 함께 사용할 수 있습니다.

`--color-type-edges`로 **Type 참조선마다 서로 다른 색상**을 지정할 수
있습니다. 같은 타입을 참조하는 필드도 연결선별로 색을 배정합니다. 최대 12색을
쓰며, 13번째부터는 같은 팔레트를 순환합니다. 선·화살촉·접점은 같은 색을
사용하며, 정적 SVG와 `--interactive`에서 모두 동작합니다. 연결의 출발
타입·필드·도착 타입·라벨 순으로 배정하므로 import·중첩 정의 표시 옵션을 바꿔도
색상은 유지됩니다.

팔레트는 [OKLab](https://bottosson.github.io/posts/oklab/) 공간에서 기존
색들과의 최소 거리가 가장 큰 후보를 차례로 선택했습니다. sRGB 후보의 지각적
명도는 0.46–0.67, 채도는 0.10–0.24로 제한했으며, 최종 12색의 최소 OKLab 거리는
0.138입니다. 배경·파일 그룹·흰 카드에 대한 대비는 모두 **3.5:1 이상**으로,
[WCAG 비텍스트 대비 기준의
3:1](https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast.html)을
넘도록 선정했습니다. 이는 일반적인 색각에서의 구분도를 최적화한 것이며, 모든
색각 이상에서 12색 구분을 보장하지는 않습니다. 팔레트와 측정값은
`uv run python -m kaitai_svg.palette`로 재현할 수 있습니다.


### 연결선 라우터

`--router <router-name>`으로 연결선 경로 계산 방법을 선택합니다. 두 라우터
모두 노드와 파일 그룹 제목 영역을 피해 수평·수직 경로를 만들며, 노드 배치와
포트 위치는 같습니다. import·중첩 정의 표시, 색상, interactive 옵션과 함께
사용할 수 있습니다.

| 이름         | 구현               | 동작                                                                     |
| ------------ | ------------------ | ------------------------------------------------------------------------ |
| `default`    | `DefaultRouter`    | 기존 희소 격자 A* 라우팅. 옵션을 생략했을 때의 기본값                    |
| `orthogonal` | `OrthogonalRouter` | 추가 우회 트랙과 방향을 고려한 A*, 전체 경로의 혼잡을 평가하는 반복 개선 |

기본값을 `default`로 유지해 기존 출력과 처리 비용을 보존합니다. `orthogonal`은
짧은 연결부터 계산한 뒤 혼잡한 경로를 하나씩 제거하고 다시 찾습니다. 개선
여부는 **공유 선분 길이**를 먼저 비교하고, 같으면 공유 격자점의
접촉(교차·닿음), 꺾임 수, 전체 길이의 가중 비용을 비교합니다. 포트에 붙는 고정
선분도 평가에 포함하며, 이 목적값이 좋아지는 변경만 채택합니다. 같은 입력과
옵션에는 결정적으로 같은 경로를 만듭니다.

기본 `OrthogonalRouter`의 개선 단계는 최대 2회이며, 연결당 A* 탐색은 최초
계산을 포함해 최대 3회입니다. 추가 격자와 반복 평가 때문에 `default`보다 계산
시간과 메모리가 늘 수 있습니다. 전체 최적해, 교차 제거, 모든 그래프에서 기존
라우터보다 나은 결과를 보장하지 않으므로 도면에 맞춰 선택하세요.

인터페이스와 구현은 [`src/kaitai_svg/routing.py`](src/kaitai_svg/routing.py)에
있습니다. 꺾임·교차·공유 경로·간격을 함께 고려하는 설계는
[Adaptagrams/libavoid 공식 라우팅 매개변수
문서](https://www.adaptagrams.org/documentation/namespaceAvoid.html)를
참고했습니다. 이 프로젝트는 자체 Python 라우터를 사용하며 libavoid나 외부 실행
파일에 의존하지 않습니다.

라우터별 교차 수·공유 선분 길이·꺾임 수·경로 길이·계산 시간은 다음 명령으로
비교할 수 있습니다. 결과는 입력과 실행 환경에 따라 달라집니다. 모든 관계를
포함하려면 `--show-imports --show-nested-types`를 추가하세요.

```sh
uv run python tests/compare_routers.py examples/sqlite3.ksy
```


## SVG 보기

생성한 SVG를 브라우저에서 직접 여세요.

-   `--interactive`로 생성하면 타입과 필드에 마우스를 올리거나 Tab으로
    포커스를 이동할 때 해당 `doc`을 표시합니다.
-   타입 이름·식별자·위치·크기·표현식에는 Cascadia Mono / Consolas 계열
    monospace를 사용하고, 문서 툴팁과 설명에는 Segoe UI 계열 산세리프를
    사용합니다. 한글 대체 글꼴과 각 글꼴의 폭을 고려해 줄바꿈합니다.
-   각 타입을 `pos | size | type | id` 표로 표시하고, 헤더의 **SEQ SIZE**에
    순차 읽기 크기를 표시합니다. 조건, 반복, switch 분기와 인스턴스 표현식은
    해당 행 아래에 표시합니다.
-   같은 `.ksy` 파일에서 온 타입과 enum은 파일명 테두리 안에 모읍니다. 타입
    내부는 `params`, `seq`, `instances` 구역으로 나눕니다. enum은 `value | id`
    두 컬럼만 표시하고 위치·크기 컬럼과 SEQ SIZE 배지는 생략합니다.
-   타입 참조와 enum 참조를 서로 다른 연결선으로 나타냅니다. import 선은
    `--show-imports`, 부모와 중첩 정의 사이의 선은 `--show-nested-types`
    옵션을 지정했을 때만 나타납니다.
-   옵션으로 표시한 import는 **주황색 파선 + 열린 화살표**, 중첩 타입은
    **청록색 실선 + 부모 쪽 빈 마름모**로 구분합니다. interactive 모드에서
    호버·포커스로 강조해도 관계의 색상과 선 모양은 유지됩니다.
-   연결마다 접점과 경로를 분리하고, 선이 교차하는 곳은 배경색 간격으로
    구분합니다. 상단 범례로 연결 종류를 구별합니다. interactive 모드에서는
    필드에 마우스를 올리거나 포커스를 주면 그 필드의 연결만 강조합니다.
    연결선을 호버하거나 Tab으로 포커스를 주면 연결 라벨과 출발·도착 타입
    문서를 표시합니다.
-   interactive 모드에서 휠로 확대/축소하고 드래그로 이동할 수 있습니다. SVG
    안의 초기화 버튼으로 전체 보기를 복원합니다.


### 표의 위치와 크기

| 표시                       | 의미                                                                        |
| -------------------------- | --------------------------------------------------------------------------- |
| `pos: 2`                   | 타입 시작 기준 바이트 오프셋 2                                              |
| `pos: 1.5`                 | 1바이트와 5비트를 읽은 위치. 소수점 숫자가 아닌 `byte.bit` 표기             |
| `size: 2 B`, `size: 5 bit` | 해당 필드가 읽는 바이트 수 또는 비트 수                                     |
| `18–22 B`                  | 조건에 따른 최소·최대 크기 범위. 범위 안의 모든 길이가 가능하다는 뜻은 아님 |
| `count × 4 B`, `length B`  | 원문 표현식에 의존하는 길이                                                 |
| `?`, `to EOS`, `variable`  | 정적으로 확정할 수 없는 길이, 스트림 끝까지, 가변 반복                      |
| `—`                        | 바이트를 읽지 않는 매개변수·계산 인스턴스 또는 enum 값                      |

기본 타입, 고정 `size`·`contents`, 참조 타입, 비트 필드, 상수 반복은 정적으로
계산합니다. 바이트 읽기 전 정렬은 [Kaitai의 비트 필드
규칙](https://doc.kaitai.io/user_guide.html#_bit_sized_integers)을 따릅니다.
`instances`의 `pos`는 KSY에 지정된 스트림 기준 식을 그대로 표시하며, 재읽기
크기는 **SEQ SIZE**에 더하지 않습니다. enum의 `value` 칸에는 숫자 값을
표시합니다. 표현식을 실행하거나 실제 바이너리를 읽지 않으므로, 조건과 재귀
등에서 계산할 수 없는 값은 확정된 숫자로 표시하지 않습니다.


## 검증과 import

모든 입력과 재귀적으로 불러온 import를 [Kaitai 공식 JSON
Schema](https://github.com/kaitai-io/ksy_schema)로 검증합니다.

JSON Schema에 더해 중복 YAML 키, 누락된 `meta.id`, 알 수 없는 타입/enum 참조,
중복 식별자, 반복 표현식 누락도 검사합니다. 오류에는 파일 경로와 문제가 있는
스키마 위치를 표시합니다. **Kaitai 표현식의 문법 및 타입 전체를 컴파일러처럼
검사하거나 바이너리 파일을 해석하지는 않습니다.** 표현식은 원문으로
표시합니다.

```yaml
meta:
  id: packet
  imports:
    - common           # 현재 .ksy 디렉터리의 common.ksy
    - /network/header  # -I 또는 KSPATH의 network/header.ksy
seq:
  - id: header
    type: header
    doc: 패킷의 헤더입니다.
  - id: payload
    type: common::payload
```

상대 import는 **import를 선언한 파일의 디렉터리**를 기준으로 찾습니다. `/`로
시작하는 라이브러리 import는 `-I`를 지정한 순서, 이어서 `KSPATH` 순서로
찾습니다. `KSPATH` 구분자는 Windows에서 `;`, Linux에서 `:`입니다. import
이름에서는 `.ksy` 확장자를 생략합니다. import 순환과 자기 참조 타입을
처리하며, 불러온 타입도 같은 SVG에 포함합니다. import 파일 자체의 별도 SVG가
필요하면 해당 파일도 glob 또는 인자로 입력하세요.


## Python API

```python
from pathlib import Path
from kaitai_svg import convert
from kaitai_svg.loader import load_graph
from kaitai_svg.render import render_svg

source = Path("examples/sqlite3.ksy")
output = convert(
    source, interactive=True, router="orthogonal",
    color_type_edges=True, hide=["vlq_base128_be.ksy"], treeshake=True,
)

# SVG 문자열만 필요한 경우
graph = load_graph(source)
svg_text = render_svg(graph, interactive=True)
```

`convert`와 `render_svg`에는 CLI의 `show_imports`, `show_nested_types`,
`color_type_edges`, `router`, `hide`, `treeshake` 옵션을 같은 이름의 키워드 인수로 전달할
수 있습니다. import 검색 경로는 `convert`와 `load_graph`의 `import_paths`로
지정합니다. `router="default"`가 기본값이며 등록되지 않은 이름은
`ValueError`를 발생시킵니다. Python API에는 이름 대신 구체적인 `Router` 하위
**클래스**도 전달할 수 있습니다.

```python
from pathlib import Path
from kaitai_svg import OrthogonalRouter, convert


class CustomRouter(OrthogonalRouter):
    # 기존 장애물 회피 구현을 상속하고 비용 가중치만 조정합니다.
    bend_penalty = 70
    crossing_penalty = 240


output = convert(Path("examples/sqlite3.ksy"), router=CustomRouter)
```

`Router`를 직접 구현할 때는 추상 메서드
`__init__(nodes, anchors, groups=None)`와 `route(start, end)`를 구현합니다.
인스턴스는 도면마다 새로
생성되며, `anchors`에는 각 연결의 시작·끝 좌표가 입력 순서대로 들어 있습니다.
`route`는 두 앵커를 포함하는 수평·수직 점 목록을 반환하고 다음 연결을 위해
사용 경로를 예약합니다. 노드 사각형과 파일 그룹 제목 영역을 장애물로 취급해야
합니다.

출발 앵커는 출발 노드 오른쪽 포트에서 **x + 18**, 도착 앵커는 도착 노드 왼쪽
포트에서 **x − 18**에 있습니다. `layout_graph`가 포트와 앵커 사이의 고정 수평
선분을 앞뒤에 붙입니다. 사용자 라우터도 이 선분의 방향과 장애물 회피를
유지해야 합니다. `route_all(connections)`의 기본 구현은 입력 순서대로
`route`를 호출합니다. 전체 최적화를 위해 재정의하면 내부 탐색 순서를 바꿀 수
있지만, 반환값은 **입력 순서와 같은 순서로 연결당 경로 하나씩**이어야 합니다.
사용자 클래스는 Python API에서 전달하며 CLI는 `ROUTERS`에 등록된 이름만
받습니다.


## 개발 및 테스트

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
uv build
```

표준 테스트는 `unittest`를 사용하며, 포함된 SQLite 스키마와 상대 import의
변환도 검사합니다. Node.js가 설치되어 있으면 생성된 SVG 스크립트의 상호작용
검사를 추가로 실행합니다. Node.js가 없으면 해당 검사만 건너뜁니다.

실제 화면과 글꼴, 포인터·키보드 동작은
[Playwright](https://playwright.dev/python/docs/intro) 브라우저 검사로 확인할
수 있습니다. `browser_check.py`는 테스트 fixture와 SQLite 예제의
정적·interactive SVG를 Chromium 또는 Firefox에서 검사합니다.
`browser_type_colors.py`는 연결선 색상과 강조를 확인합니다.

```sh
uv run --with playwright==1.64.0 python -m playwright install chromium firefox
uv run --with playwright==1.64.0 python tests/browser_check.py --browser chromium
uv run --with playwright==1.64.0 python tests/browser_check.py --browser firefox
uv run --with playwright==1.64.0 python tests/browser_type_colors.py --browser chromium
uv run --with playwright==1.64.0 python tests/browser_type_colors.py --browser firefox
```

결과 JSON과 스크린샷은 Git에서 제외되는 `.scratch/browser-qa/`에 저장됩니다.
Playwright와 브라우저는 선택형 개발 도구이며 패키지의 실행 의존성이 아닙니다.
