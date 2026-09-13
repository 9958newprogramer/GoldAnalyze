package com.goldanalyze.controlplane.market;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

import com.goldanalyze.controlplane.market.dto.KlineResponse;
import org.springframework.web.bind.annotation.RequestParam;

import java.time.LocalDate;

import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;


@RestController
@RequestMapping("/api/v1/market")
public class MarketController {

    private final MarketDataService marketDataService;

    public MarketController(MarketDataService marketDataService) {
        this.marketDataService = marketDataService;
    }

    @GetMapping("/test")
    public List<Map<String, Object>> test() {
        return marketDataService.testQuery();
    }

    /**
     * 按标的和日期范围查询日线 K 线。
     */
    @GetMapping("/kline")
    public List<KlineResponse> getKlines(
            @RequestParam String symbol,
            @RequestParam(defaultValue = "1d") String interval,
            @RequestParam LocalDate start,
            @RequestParam LocalDate end
    ) {
        KlineInterval parsedInterval = KlineInterval.fromValue(interval);

        return switch (parsedInterval) {
            case ONE_DAY -> marketDataService.findDailyKlines(symbol, start, end);
            case FOUR_HOUR -> marketDataService.findFourHourKlines(symbol, start, end);
            case ONE_HOUR -> marketDataService.findOneHourKlines(symbol, start, end);
            default -> throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "Unsupported interval: " + interval
            );
        };
    }


}