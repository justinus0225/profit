"""데이터 품질 파이프라인 (ARCHITECTURE.md P10).

3단계 파이프라인:
1. 이상치 탐지 (Z-Score / IQR)
2. 데이터 힐링 (보간, Forward Fill, MA 대체)
3. 삽입 전 검증 (NOT NULL, 가격>0, 볼륨≥0)
"""
