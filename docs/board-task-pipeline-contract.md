# Board Task Pipeline Contract

本文档是已有板书任务流的收敛基线。它不描述新功能，而是把当前应该遵守的工程边界写清楚，避免继续用零散补丁规则推进。

## 两条链路的边界

空白板书链路只处理“还没有右侧板书”的情况。用户说“我想学 X”时，系统应该先整理学习需求；用户明确点击或发起“生成板书”时，才进入板书生成。空白链路不应该走已有板书的定位、改写、讲解、互动任务单。

已有板书链路只处理“右侧已经有内容”的情况。用户想写、改、讲解、互动时，本轮必须先形成 `BoardTaskRequirementSheet`，再定位目标，再决定路线。Chatbot 不应该绕过任务单直接讲，也不应该在位置不清楚时猜测写入。

## 动作触发规则

`write` 表示给已有板书补新内容，例如“补充一个例子”“新增一段练习”。它可以追加到全文，也可以围绕一个已定位位置补充，但必须经过 document write gate。

`edit` 表示改已有内容，例如“把这段改简单点”“润色第二节”。它必须先有明确目标位置。找不到目标时要澄清；连续找不到时，旧编辑任务应标记为未执行，再询问是否改为写入。

`explain` 表示讲解已有内容，例如“讲解这一段”“解释第 2 节”“讲解第 2 题”。它必须先通过板书定位和讲解 directive。没有 directive 时，Chatbot 只能澄清，不能凭常识直接讲。

`chat` 表示围绕已有板书开始互动，例如“你问我答”“按规则练习”。它也必须先定位到互动依据，并保存 `InteractionSession`。

`generate_board` 只属于空白板书链路。已有板书存在时，普通“生成/创建板书”不能绕过已有板书任务流去重建整份板书。

## 优先级

用户选区优先级最高。只要前端传入板书选区，选区就是本轮默认目标，不应再用文本里的泛指“这一段/这里”覆盖它。

已有任务单里的 `target_location` 次之。一个任务已经定位过时，后续确认、继续、执行应该复用同一位置，保证历史可追溯。

明确的 `target_hint` 再次之，例如“第 2 节”“练习题”“定义部分”。它需要进入定位器，不等于已经可以执行。

`whole_document` 只在用户明确说“全文、整篇、所有小节、全部内容”时生效。不能把模糊请求默认扩大为全文。

资料引用只提供依据，不自动替代板书目标。用户说“根据资料解释这段”时，资料是参考来源，“这段”的板书定位仍然必须成立。

## Sequence Explanation

顺序讲解只在用户明确要求集合型讲解时触发，例如“讲解所有小节”“逐个讲”“为我讲解练习题”。如果用户说“讲解第 2 题”，这是单点讲解，不应启动 sequence。

已有 sequence 时，“继续”推进到下一个讲解单元；“不用继续了”“停止”“退出”结束 sequence。其他追问如果仍围绕当前单元，应该保持在当前单元内回答。

## Document Write Gate

允许写右侧板书必须同时满足：

- 当前链路是已有板书任务流里的 `write` 或已确认的缺内容写入。
- 任务单已完整，至少明确“写什么”。
- 如果是围绕局部写入，目标位置已经选中或定位成功。
- 如果系统判断板书没有对应内容，必须先进入 `await_write_confirmation`，得到用户确认后才写。

禁止写右侧板书的情况：

- 用户只是普通聊天或讲解请求。
- 目标位置不确定。
- 用户提到资料，但没有明确要求写入。
- Chatbot 只拿到了模型回答，没有拿到板书编辑结果。
- 任务单缺项，或用户取消了待确认写入。

不确定时必须澄清，不能猜测写入。

## Commit Metadata 必填字段

已有板书任务流产生 commit 时，至少要能回答三件事：用户说了什么、系统走了哪条任务路线、任务现在是否还活着。

通用字段：

- `kind`
- `user_message`
- `assistant_message`
- `assistant_message_source`
- `active_requirement_sheet_after`
- `active_board_task_sheet_after`

board task 字段：

- `board_task_sheet`
- `board_task_run_id`
- `board_task_version_id`
- `board_task_phase`
- `board_task_route`
- `board_task_cleared`

定位相关字段：

- `board_search_evidence`
- `resolved_focus` 或 `focus_candidates`

讲解相关字段：

- `board_explanation_directive`

写入/编辑相关字段：

- `board_edit_operation`
- `board_edit_summary`
- `target_scope`
- `recent_board_edit_focus`

sequence 相关字段：

- `active_interaction_session_after`
- `explanation_sequence`
- `explanation_sequence_mode`

## Golden Fixture 基线

`apps/api/tests/fixtures/board_task_turn_cases.yml` 记录 12 个代表性 turn。测试只锁定当前 helper 的识别结果和契约缺口，不修改业务逻辑。

当前已知缺口：

- “讲解这一段”在有选区时，任务单仍可能因为缺少 `question_or_topic` 停在澄清阶段。
- “补充一个例子”在 `chatbot` 侧更像 `expand_target`，在 `board_task_manager` 侧更像 `write`，说明动作判断仍有双源。
- sequence 的继续/退出由 interaction session 处理，不应该被已有板书任务流重新解释成普通任务。
