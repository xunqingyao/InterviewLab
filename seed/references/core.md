# InterviewLab knowledge snippets. Self-authored summaries for evaluator retrieval.

## rag.evaluation_design
评估检索不能只看“感觉更好”。应固定测试集、固定生成配置，只改变检索变量，并报告对照条件。

## backend.idempotency
HTTP 幂等键解决客户端重试；领域唯一约束防止不同 key 的重复业务结果；job fencing 阻止过期 Worker 覆盖新结果。

## backend.jobs
SKIP LOCKED 让多个消费者领取不同任务。租约提供接管机会，fencing token 让旧执行者失权。两者不能互相替代。

## ai.agent_state
一场面试不应是一棵一直悬挂的大图。每次提问或评估是短 Agent 图；用户等待由 session/turn 状态机承载。checkpoint 不是业务账本。
