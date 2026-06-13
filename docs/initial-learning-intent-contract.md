# Initial Learning Intent Contract

本文档描述空白板书第一层新增的前置判别。它是后续开发的架构合同，不是当前运行代码说明。

## 目标

当右侧 `board_document` 为空时，系统不能把所有学习意向都直接送进完整学习需求清单。它必须先判断用户是在学习知识内容，还是想做练习型教学，并判断用户目标颗粒度是否已经足够生成第一版板书。

这个判别解决的是通用学习形态和目标颗粒度问题，不解决任何具体学科、教材、考试或 demo。

## 输入边界

`InitialLearningIntentGate` 只在空白板书第一层启用，输入可以包括：

- 当前用户消息。
- 当前 lesson / course metadata 中与本轮学习目标有关的轻量上下文。
- 已经存在但尚未冻结的第一层需求清单状态。

它不得读取右侧板书全文，因为空白板书链路没有可学习板书内容；也不得调用 BoardEditor 或直接生成板书正文。

## 输出字段

门禁输出必须是结构化状态：

- `learning_mode`
  - `learn_concept`：用户想理解、学习或开始讲某个知识内容。
  - `practice_activity`：用户想通过练习、测验、对话、角色互动、纠错或类似活动学习。
  - `undecided`：用户只表达了想学，但尚未说明学习形态。
- `target_granularity`
  - `specific_concept`：目标已经小到可以形成第一版板书的知识点、问题或明确主题单元。
  - `broad_domain`：目标仍是宽泛领域、方向或长期学习范围。
  - `ambiguous`：无法可靠判断目标颗粒度。
- `next_action`
  - `freeze_minimal_and_generate_board`
  - `ask_specific_concept`
  - `collect_practice_requirements`
  - `ask_learning_mode`
- `trace_reason`：用通用信号说明为什么选择该动作，以及为什么没有选择其他动作。

## 状态转移

### 知识内容，且目标足够小

当 `learning_mode=learn_concept` 且 `target_granularity=specific_concept`：

1. Requirement Manager 形成 `minimal frozen requirement`。
2. 写入 ready / frozen 版本和事件。
3. BoardEditor 只消费 frozen requirement payload 生成右侧板书。
4. 生成成功后写 lesson commit，并记录门禁输出、requirement run / frozen version。
5. Chatbot 只做承接回复，不自行生成板书正文。

### 知识内容，但目标过大

当 `learning_mode=learn_concept` 且 `target_granularity=broad_domain`：

1. 不生成默认课程路径。
2. 不提前询问练习型字段。
3. Chatbot 只追问用户具体想学的知识点、问题或范围。
4. 如果用户随后给出足够小的知识目标，再进入最小冻结需求生成。

### 练习型教学

当 `learning_mode=practice_activity`：

1. 进入学习需求清单澄清。
2. 优先补齐练习内容、当前水平、目的场景、练习形式、反馈方式和成功标准。
3. 清单未完整时只追问关键缺项。
4. 清单完整或用户强制开始时，按第一层 frozen requirement 流程生成。

### 学习形态不明

当 `learning_mode=undecided` 或 `target_granularity=ambiguous`：

1. Chatbot 询问用户是想学习知识内容，还是做练习型教学。
2. 用户选择知识内容后，继续判断目标颗粒度。
3. 用户选择练习后，进入练习型需求清单澄清。

## 角色边界

- Chatbot 负责询问、澄清和承接，不直接生成右侧板书正文。
- Requirement Manager 负责维护 `LearningRequirementSheet`、门禁状态、版本和事件。
- BoardEditor 只在拿到 frozen requirement payload 后生成板书。
- Prompt 可以帮助判断，但不能替代门禁状态、版本历史、冻结快照和 commit metadata。

## 通用性约束

禁止把门禁写成：

- 学科关键词分支。
- 教材关键词分支。
- 考试关键词分支。
- demo 或单一句式补丁。
- 固定课程路径、固定讲义或固定 HTML。

允许使用的信号必须是通用学习形态、目标颗粒度、内容形态或用户目标，例如“用户是否请求练习活动”“目标是否已是单个知识点或明确问题”“是否仍是宽泛领域”。

## DecisionTrace 要求

任何实现都必须在 response 或 commit metadata 中记录：

- `learning_mode`
- `target_granularity`
- `next_action`
- `trace_reason`
- `requirement_phase`
- 是否生成了 `minimal frozen requirement`
- 是否调用了 BoardEditor

如果某个信号被识别但没有成为最终动作，trace 也应说明拒绝原因，避免多个自然语言规则静默抢占。

## 测试要求

实现前必须先写 golden fixtures：

- 每个正例至少配两个反例。
- 正例和反例都必须表达通用形态，不得依赖某个学科、教材或 demo。
- 至少覆盖：足够小的知识目标直接冻结生成、宽泛知识目标追问具体知识点、练习型教学进入清单澄清、学习形态不明先询问学习形态。

没有测试和 trace，不允许把该门禁接入默认第一层链路。
