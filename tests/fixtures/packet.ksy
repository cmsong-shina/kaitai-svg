meta:
  id: packet
  title: Example message packet
  endian: le
  imports:
    - common
doc: |
  가져온 타입, 열거형, 조건부 타입과 중첩 타입을 보여 주는 패킷입니다.
  필드 위에 마우스를 올리면 설명이 표시됩니다.
seq:
  - id: magic
    contents: [75, 83, 89, 0]
    doc: 파일 시작을 나타내는 "KSY" 시그니처입니다.
  - id: version
    type: u1
    doc: 패킷 형식 버전입니다.
  - id: sender
    type: common::identifier
    doc: 공통 스키마에서 가져온 발신자 식별자입니다.
  - id: kind
    type: u1
    enum: common::message_kind
    doc: 페이로드에 저장된 데이터의 종류입니다.
  - id: length
    type: u2
    doc: 페이로드의 길이(바이트)입니다.
  - id: payload
    type:
      switch-on: kind
      cases:
        'common::message_kind::text': text_payload
        'common::message_kind::bytes': byte_payload
    size: length
    doc: 메시지 종류에 따라 문자열 또는 바이트 배열로 해석합니다.
types:
  text_payload:
    doc: UTF-8 텍스트를 저장합니다.
    seq:
      - id: text
        type: str
        encoding: UTF-8
        size-eos: true
        doc: '<tag> & "quotes" 같은 문자도 안전하게 표시됩니다.'
  byte_payload:
    doc: 바이트 레코드의 목록을 저장합니다.
    seq:
      - id: records
        type: record
        repeat: eos
        doc: 페이로드가 끝날 때까지 반복되는 레코드입니다.
    types:
      record:
        doc: 하나의 바이트를 가진 중첩 타입입니다.
        seq:
          - id: value
            type: u1
            doc: 레코드의 원시 바이트 값입니다.
instances:
  signature_copy:
    pos: 0
    size: 4
    doc: 파일의 시작 위치에서 다시 읽은 시그니처입니다.
  is_text:
    value: kind == common::message_kind::text
    doc: 텍스트 메시지인지 나타내는 계산된 값입니다.
