package com.goldanalyze.controlplane.job;

import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/jobs")
public class JobController {

    private final JobService jobService;

    public JobController(JobService jobService) {
        this.jobService = jobService;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public AgentJob createJob(@Valid @RequestBody CreateJobRequest request) {
        return jobService.createJob(
                request.question(),
                request.idempotencyKey()
        );
    }

    @GetMapping("/{jobId}")
    public AgentJob getJob(@PathVariable String jobId) {
        return jobService.getJob(jobId);
    }
}
