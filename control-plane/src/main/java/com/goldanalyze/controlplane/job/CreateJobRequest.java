package com.goldanalyze.controlplane.job;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record CreateJobRequest(
        @NotBlank String question,

        @NotBlank @Size (min=16,max = 128) 
        String idempotencyKey
) {
}
