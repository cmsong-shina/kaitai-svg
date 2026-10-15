meta:
  id: common
  title: Shared packet definitions
  endian: le
doc: 여러 스키마가 함께 사용하는 메시지 종류와 식별자입니다.
enums:
  message_kind:
    1:
      id: text
      doc: UTF-8 문자열을 담는 메시지입니다.
    2:
      id: bytes
      doc: 임의의 바이트 배열을 담는 메시지입니다.
types:
  identifier:
    doc: 발신자와 수신자를 구별하는 32비트 식별자입니다.
    seq:
      - id: value
        type: u4
        doc: 식별자의 실제 숫자 값입니다.
