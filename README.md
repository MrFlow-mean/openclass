# 开放课堂（OpenClass）

<p align="center">
  <img src="docs/assets/openclass-product-cover.png" alt="OpenClass 产品封面" width="280" />
</p>

开放课堂（OpenClass）是一个面向学习内容生成、讲义编辑、资料管理和交互式学习的 AI 学习工作台。

它不是普通聊天机器人，也不是单纯的富文本编辑器。OpenClass 的核心产品模型是：

* 左侧 Chatbot 负责理解学习意图、建议方向、追问缺项、承接下一步；
* 右侧 BoardEditor 负责生成、扩写、改写正式学习内容；
* Requirement Sheet 负责记录可追踪的学习需求状态；
* FocusResolver / Board AI 负责在已有板书中定位目标内容，并决定写、改、讲或互动；
* 版本历史和分支系统负责保留每次内容变化，支持回退和探索不同讲法。

## 产品核心模型

OpenClass 的每一轮用户输入，第一步不是“直接回答”，而是判断右侧板书是否为空。

```txt
board_document 为空
→ 第一层：从零开始建立学习内容

board_document 非空
→ 第二层：围绕已有板书执行具体任务
```

这使 OpenClass 的学习流程分成两层：

第一层用于从零建立学习内容。系统会判断用户是在普通聊天，还是确实要学习、练习、生成学习材料。只有进入学习链路时，系统才会维护 LearningRequirementSheet，并在合适时生成右侧板书。

第二层用于围绕已有板书执行任务。系统不再重新收集整篇学习需求，而是进入 BoardTaskRequirementSheet，记录目标位置、动作类型、问题或主题内容、特殊交互方式要求，然后通过 FocusResolver 定位板书内容，再交给 Board AI 执行 write / edit / explain / chat。

## 当前 AI 工作流状态

当前 OpenClass 已经接入新的两层学习工作流骨架：

```txt
ChatTurnGate
→ ordinary_chat
→ initial_learning
→ initial_board_generation
→ existing_board_task
→ resource_reference
```

其中：

* ordinary_chat：普通聊天，不污染学习需求，不自动生成板书；
* initial_learning：空白板书下识别到学习、练习或学习材料生成意图；
* initial_board_generation：空白板书下明确进入板书生成；
* existing_board_task：已有板书下围绕现有内容执行写、改、讲、互动；
* resource_reference：处理资料引用、章节确认和资料驱动生成。

## 空白板书链路

当右侧板书为空时，OpenClass 处在“从零到有”的阶段。这时系统不会直接进入局部讲解、局部修改或互动练习，因为还没有可依附的正式学习内容。

空白板书下主要分为两类：

```txt
普通聊天
→ 不更新 LearningRequirementSheet
→ 不生成板书
→ 只做自然对话

学习 / 练习 / 生成学习材料意图
→ 进入 InitialLearningWorkModeDecision
```

进入学习链路后，系统会判断四种初始学习模式。

### knowledge_board

knowledge_board 对应用户想学一个相对聚焦的新知识点、概念、方法、步骤或清晰问题。

例如：

```txt
我想学递归的基本思想
我想理解过去将来时
我想学贝叶斯公式是什么意思
```

这类请求不应该继续追问完整课程需求，而应该构造最小需求清单，冻结需求快照，然后由 BoardEditor 生成一份聚焦知识板书。

链路：

```txt
knowledge_board
→ 构造最小需求清单
→ 记录知识点、用户原始意图、可见背景
→ 冻结需求快照
→ BoardEditor 生成聚焦知识板书
→ Chatbot 询问是否开始讲解
```

生成板书后，Chatbot 不应该自动讲第一节，而是承接用户下一步。

### narrow_topic

narrow_topic 对应用户确实想学新知识，但主题过宽，无法直接生成聚焦板书。

例如：

```txt
我想学机器学习
我想学经济学
我想学编程
```

链路：

```txt
narrow_topic
→ 构造最小清单状态
→ 只追问一个缩小问题
→ 不生成板书
```

系统追问的目标不是机械收集表单，而是帮助用户把大方向压缩成一个可以开始学习的知识点。

### practice_artifact

practice_artifact 对应练习、测验、角色扮演、情景对话、案例任务、改错任务等可操练材料。

例如：

```txt
生成一篇情景对话课文
给我一组练习题
设计一个角色扮演任务
出一个测验
给我一个案例任务
```

这类请求需要完整 LearningRequirementSheet，因为练习材料必须匹配学习者背景、难度、场景、材料形态、能力点和成功标准。

链路：

```txt
practice_artifact
→ 维护完整 LearningRequirementSheet
→ 判断练习目标、学习者水平、材料形态、约束是否足够
→ 清单不够：只问最关键缺项
→ 清单足够：冻结需求
→ BoardEditor 生成练习板书
```

practice_artifact 的判断依据不是学科，而是产物形态。

### unknown

unknown 对应用户表达了学习意愿，但目的还不清楚。

例如：

```txt
我最近想开始学点东西，但不知道怎么开始
```

链路：

```txt
unknown
→ 不生成板书
→ 不进入完整需求清单
→ Chatbot 给 2-3 个学习方向或学习产物方向建议
→ 只问一个选择 / 缩小问题
```

这个分支让系统承担产品引导责任，但不会越权生成板书。

## 已有板书链路

当右侧已经有板书时，系统进入第二层任务链路。

这时用户不再是在“从零开始要学什么”，而是在围绕已有内容做任务。因此系统不应该继续使用第一层 LearningRequirementSheet 作为主状态，而应该进入 BoardTaskRequirementSheet。

BoardTaskRequirementSheet 记录四个核心字段：

```txt
目标位置
动作类型
问题 / 主题内容
特殊交互方式要求
```

然后执行：

```txt
BoardTaskRequirementSheet
→ FocusResolver 定位目标位置
→ Board AI route decision
→ write / edit / explain / chat
```

其中：

* write：向板书中写入或追加内容；
* edit：改写、扩写、简化或优化已有内容；
* explain：围绕板书定位结果进行讲解；
* chat：按用户指定规则围绕板书内容互动。

这里有一个关键边界：Chatbot 不能直接读取整篇板书后自由讲解。它必须等板书侧给出目标摘录、讲解边界和 directive，才能回答用户。

## 当前稳定能力与实验能力

### Stable

* 课程包、lesson、资料和文档编辑工作台；
* 右侧富文本 BoardEditor；
* DOCX 导入导出；
* SQLite 持久化；
* commit / branch / restore 版本历史；
* ChatTurnGate 基础入口分流；
* LearningRequirementSheet 基础需求状态；
* BoardTaskRequirementSheet 基础任务状态；
* 已有板书下的写、改、讲、互动 route 骨架；
* 资料上传、章节抽取和资料引用确认。

### Experimental

* practice_artifact 完整需求链路；
* resource-backed board generation；
* interaction session；
* sequential explanation；
* Realtime 语音交互；
* 多 provider 模型选择；
* 复杂问题的隐藏强推理工具；
* BoardTeachingGuide / BoardTeachingProgress 与新工作流的深度整合。

### Legacy / Reserved

部分旧教学工作流 schema 保留用于兼容历史数据和未来迁移，不代表所有旧 AI 教学编排都已经作为稳定能力重新接回。
