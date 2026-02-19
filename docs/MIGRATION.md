# Migration Plan: Sync to Async Event-Driven Architecture

This document outlines a phased migration strategy for transitioning from synchronous webhook handlers to the async event-driven architecture.

## Overview

The migration follows a **gradual rollout** approach to minimize risk and ensure a smooth transition:

1. **Phase 1**: Deploy parallel (10% traffic) - Low-risk validation
2. **Phase 2**: Increase to 50% - Confidence building
3. **Phase 3**: Full migration (100%) - Complete cutover
4. **Phase 4**: Cleanup - Remove old infrastructure

Each phase includes rollback procedures to quickly revert if issues arise.

## Architecture Comparison

### Current (Sync)
```
HubSpot Webhook → API Gateway → Lambda Handler → Partner Central API
                                      ↓
                              200 OK (after processing)
```

**Characteristics:**
- Synchronous processing (30-60s response time)
- Direct API calls to Partner Central
- Failures return errors to HubSpot
- No built-in retry mechanism
- Limited observability

### Future (Async)
```
HubSpot Webhook → API Gateway → Receipt Lambda → SQS Queue → Processor Lambda → Partner Central API
                                      ↓                              ↓
                                 200 OK (instant)              DLQ (failures)
                                                                     ↓
                                                            CloudWatch Alarms
```

**Characteristics:**
- Fast webhook response (<100ms)
- Reliable message queuing (14-day retention)
- Automatic retry (3 attempts)
- DLQ for failed messages
- Comprehensive monitoring and alarms

## Benefits of Async Architecture

1. **Reliability**
   - Automatic retries on transient failures
   - Message persistence (14 days)
   - DLQ captures all failures

2. **Performance**
   - Fast webhook acknowledgment (<100ms)
   - No HubSpot webhook timeouts
   - Better throughput under load

3. **Scalability**
   - Decoupled components
   - Independent scaling of receipt and processing
   - Handle traffic spikes gracefully

4. **Observability**
   - Comprehensive CloudWatch metrics
   - Real-time alarms
   - Detailed error tracking
   - Processing latency visibility

5. **Maintainability**
   - Clean separation of concerns
   - Easier testing and debugging
   - Modular processor functions

## Prerequisites

Before starting migration:

- [ ] Async architecture deployed (see `infrastructure/README.md`)
- [ ] CloudWatch dashboard configured
- [ ] Alarms tested and verified
- [ ] Team trained on new architecture
- [ ] Rollback procedures documented and tested
- [ ] Success criteria defined

## Phase 1: Deploy Parallel (10% Traffic)

**Goal**: Validate async architecture with low-risk traffic

**Duration**: 1 week

### Steps

1. **Deploy async infrastructure** (if not already done)
   ```bash
   sam deploy \
     --template-file infrastructure/async-architecture.yaml \
     --stack-name hubspot-async-sync-production \
     --parameter-overrides Environment=production ...
   ```

2. **Configure HubSpot webhook splitting**
   
   Option A: Use API Gateway weighted routing (recommended)
   ```bash
   # Update API Gateway deployment with canary settings
   aws apigateway create-deployment \
     --rest-api-id <api-id> \
     --stage-name production \
     --canary-settings \
       percentTraffic=10 \
       useStageCache=false
   ```

   Option B: Manual webhook duplication
   - Keep existing webhook at 100%
   - Add new webhook at async endpoint
   - Use HubSpot's webhook filtering to send ~10% of events

3. **Monitor for 48 hours**
   - Check CloudWatch dashboard daily
   - Review error logs
   - Compare success rates between sync and async
   - Verify DLQ is empty

4. **Validate processing**
   - Spot-check deals in HubSpot
   - Verify Partner Central opportunities created
   - Compare data consistency between sync and async

### Success Criteria

- [ ] Receipt handler response time < 100ms (p99)
- [ ] Event processor error rate < 1%
- [ ] Zero messages in DLQ
- [ ] Queue age < 30 seconds (p99)
- [ ] All test events processed correctly
- [ ] No customer-reported issues

### Rollback Procedure

If issues arise:

1. **Immediate rollback** (< 5 minutes)
   ```bash
   # Remove canary deployment
   aws apigateway delete-deployment \
     --rest-api-id <api-id> \
     --deployment-id <canary-deployment-id>
   
   # Or simply remove the async webhook from HubSpot
   ```

2. **Investigate issues**
   - Review CloudWatch logs
   - Check DLQ messages
   - Identify root cause

3. **Fix and retry**
   - Deploy fixes
   - Restart Phase 1

## Phase 2: Increase to 50% Traffic

**Goal**: Build confidence with majority traffic

**Duration**: 1 week

### Prerequisites

- Phase 1 completed successfully
- All success criteria met
- No outstanding issues

### Steps

1. **Increase traffic to 50%**
   ```bash
   aws apigateway update-stage \
     --rest-api-id <api-id> \
     --stage-name production \
     --patch-operations \
       op=replace,path=/canarySettings/percentTraffic,value=50
   ```

2. **Enhanced monitoring**
   - Monitor dashboard every 4 hours for first 48 hours
   - Set up PagerDuty/Slack alerts for alarms
   - Review daily metrics summary

3. **Performance testing**
   - Send burst of test events (100-1000 events)
   - Verify queue handles load gracefully
   - Check for any throttling or errors

4. **Data validation**
   - Sample 50 deals created via async path
   - Verify 100% accuracy vs sync path
   - Check field mappings are correct

### Success Criteria

- [ ] All Phase 1 criteria still met
- [ ] Queue handled load test without issues
- [ ] No increase in error rate
- [ ] Response time remains < 100ms
- [ ] Customer satisfaction maintained

### Rollback Procedure

Same as Phase 1, but also:

1. Review any messages in DLQ
2. Manually process DLQ messages if needed
3. Root cause analysis required before retry

## Phase 3: Full Migration (100% Traffic)

**Goal**: Complete cutover to async architecture

**Duration**: 2 weeks

### Prerequisites

- Phase 2 completed successfully
- 2 weeks of stable operation at 50%
- Stakeholder approval for full cutover

### Steps

1. **Increase to 100% traffic**
   ```bash
   aws apigateway update-stage \
     --rest-api-id <api-id> \
     --stage-name production \
     --patch-operations \
       op=replace,path=/canarySettings/percentTraffic,value=100
   ```

2. **Monitor intensively for 72 hours**
   - Check dashboard every 2 hours
   - On-call engineer available
   - Daily team sync on metrics

3. **Disable old sync endpoint** (after 1 week of 100%)
   - Remove old webhook from HubSpot
   - Keep Lambda functions deployed (but unused)
   - Document decommission date

4. **Update documentation**
   - Update README.md with new architecture
   - Update API documentation
   - Create runbooks for common issues

### Success Criteria

- [ ] 1 week at 100% with no issues
- [ ] All alarms green
- [ ] DLQ empty
- [ ] Customer feedback positive
- [ ] Team comfortable with new architecture

### Rollback Procedure

1. **Quick rollback** (< 10 minutes)
   ```bash
   # Revert traffic to old endpoint
   aws apigateway update-stage \
     --rest-api-id <api-id> \
     --stage-name production \
     --patch-operations \
       op=replace,path=/canarySettings/percentTraffic,value=0
   
   # Re-enable old webhook in HubSpot
   ```

2. **Process queued messages**
   ```bash
   # Drain the queue manually if needed
   python scripts/drain_queue.py --queue-url <url>
   ```

3. **Full investigation required**
   - Detailed root cause analysis
   - Team meeting to discuss issues
   - Plan for Phase 3 retry

## Phase 4: Cleanup Old Infrastructure

**Goal**: Remove unused sync handlers and reduce costs

**Duration**: 1 week

### Prerequisites

- 2 weeks at 100% async with no rollbacks
- Stakeholder sign-off
- All documentation updated

### Steps

1. **Archive old Lambda code**
   ```bash
   # Create backup of old handlers
   git tag "pre-async-migration-$(date +%Y%m%d)"
   git push --tags
   ```

2. **Delete old Lambda functions**
   ```bash
   # Remove old sync handlers from template.yaml
   # Deploy updated template
   sam deploy --template-file template.yaml ...
   ```

3. **Remove old API Gateway routes**
   ```bash
   # Delete unused API Gateway resources
   aws apigateway delete-resource \
     --rest-api-id <api-id> \
     --resource-id <old-webhook-resource-id>
   ```

4. **Clean up monitoring**
   - Remove old CloudWatch alarms
   - Archive old log groups
   - Update dashboard to remove old metrics

5. **Update costs tracking**
   - Document cost savings
   - Update budget forecasts

### Verification

- [ ] Old Lambda functions deleted
- [ ] Old API Gateway routes removed
- [ ] Old alarms deleted
- [ ] Documentation updated
- [ ] Team trained on new architecture
- [ ] Runbooks updated

## Monitoring and Alerting

### Key Metrics

Monitor these metrics throughout migration:

1. **Receipt Handler**
   - Invocations per minute
   - Error rate (target: < 1%)
   - Duration p99 (target: < 100ms)
   - Throttles (target: 0)

2. **SQS Queue**
   - Message depth (target: < 100)
   - Age of oldest message (target: < 60s)
   - DLQ messages (target: 0)

3. **Event Processor**
   - Invocations per minute
   - Error rate (target: < 1%)
   - Duration p99
   - Success rate (target: > 99%)

4. **Business Metrics**
   - Deals created per hour
   - Sync success rate
   - Time to sync (end-to-end)
   - Customer-reported issues

### Alarm Configuration

Set up the following alarms:

- **Critical** (page immediately):
  - DLQ has > 10 messages
  - Error rate > 10%
  - Queue age > 10 minutes

- **Warning** (notify during business hours):
  - DLQ has > 0 messages
  - Error rate > 5%
  - Queue age > 5 minutes
  - Queue depth > 1000

## Common Issues and Solutions

### Issue: Messages stuck in queue

**Symptoms**: Queue age increasing, messages not processing

**Solutions**:
1. Check event processor logs for errors
2. Verify Lambda has permissions to read from queue
3. Check for Lambda throttling
4. Increase Lambda concurrency if needed

### Issue: High DLQ message count

**Symptoms**: Messages failing after 3 retries

**Solutions**:
1. Review DLQ messages for patterns
2. Common causes:
   - Invalid event format → Fix event schema
   - Partner Central API errors → Check credentials
   - Timeout → Increase Lambda timeout
3. Reprocess DLQ messages after fix:
   ```bash
   python scripts/reprocess_dlq.py --queue-url <dlq-url>
   ```

### Issue: Receipt handler slow (>100ms)

**Symptoms**: Webhook response time increasing

**Solutions**:
1. Check for cold starts → Increase memory or use provisioned concurrency
2. Review code for inefficiencies
3. Verify SQS permissions not causing delays

### Issue: Data inconsistency

**Symptoms**: Fields not syncing correctly between HubSpot and Partner Central

**Solutions**:
1. Review processor logs for warnings
2. Check field mappings in `src/common/mappers.py`
3. Validate event schema transformations
4. Compare with old sync handler behavior

## Success Metrics

Track these metrics to measure migration success:

- **Reliability**: 99.9% success rate (vs 98% for sync)
- **Performance**: <100ms webhook response (vs 30-60s for sync)
- **Latency**: <2 minutes end-to-end (vs 1-2s for sync)
- **Error Rate**: <1% (vs 2-5% for sync)
- **Cost**: Similar or lower (queue costs offset by better efficiency)

## Team Responsibilities

### On-Call Engineer
- Monitor alarms 24/7
- Respond to pages within 15 minutes
- Execute rollback if needed

### Development Team
- Review daily metrics
- Investigate any anomalies
- Fix bugs and deploy patches
- Update documentation

### Project Manager
- Weekly status updates
- Stakeholder communication
- Go/no-go decisions for each phase

### QA Team
- Validate data consistency
- Perform load testing
- Document any issues

## Timeline Summary

| Phase | Duration | Traffic % | Key Activities |
|-------|----------|-----------|----------------|
| Phase 1 | 1 week | 10% | Deploy, validate, monitor |
| Phase 2 | 1 week | 50% | Load test, build confidence |
| Phase 3 | 2 weeks | 100% | Full cutover, intensive monitoring |
| Phase 4 | 1 week | N/A | Cleanup old infrastructure |
| **Total** | **5 weeks** | | |

## Conclusion

This phased migration plan ensures a safe, gradual transition to the async event-driven architecture. By following this plan:

- **Risk is minimized** through gradual rollout
- **Issues are caught early** at low traffic levels
- **Rollback is always available** at each phase
- **Success is measurable** with clear criteria
- **Team is prepared** with training and documentation

After successful migration, the system will be more reliable, scalable, and maintainable.

## References

- [Deployment Guide](../infrastructure/README.md)
- [Architecture Documentation](../README.md)
- [Event Schema](../src/common/events.py)
- [AWS SQS Best Practices](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-best-practices.html)
