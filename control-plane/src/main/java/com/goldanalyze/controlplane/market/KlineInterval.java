package com.goldanalyze.controlplane.market;

/**
 * 支持的 K 线周期。
 */
public enum KlineInterval {

    ONE_DAY("1d"),
    FOUR_HOUR("4h"),
    ONE_HOUR("1h");

    private final String value;

    /**
     * 创建 K 线周期枚举值。
     *
     * @param value API 中使用的周期字符串
     */
    KlineInterval(String value) {
        this.value = value;
    }

    /**
     * 返回 API 使用的周期字符串。
     *
     * @return 例如 1d、4h、1h
     */
    public String getValue() {
        return value;
    }

    /**
     * 根据 API 传入的字符串解析 K 线周期。
     *
     * @param value 周期字符串
     * @return 对应的 KlineInterval
     * @throws IllegalArgumentException 不支持该周期时抛出
     */
    public static KlineInterval fromValue(String value) {
        for (KlineInterval interval : values()) {
            if (interval.value.equalsIgnoreCase(value)) {
                return interval;
            }
        }

        throw new IllegalArgumentException(
                "Unsupported interval: " + value
        );
    }
}