# Deep technical research: How do agents communicate with each other in Microso...

**Research ID:** `1fdfc4e5-ef6b-45ee-bd4b-4755277341b9`
**Query:** Deep technical research: How do agents communicate with each other in Microsoft AutoGen (v0.4+ event-driven architecture) versus AG2 (the community fork, formerly AutoGen 0.2)? Focus specifically on AGENT-INITIATED / UNPROMPTED communication — when and how an agent autonomously sends a message to another agent without a human or central orchestrator triggering each individual exchange.

Required coverage:
1. The AutoGen -> AG2 split: Microsoft AutoGen vs the ag2ai community fork. Timeline, why it forked, which API/version each maintains now (2025-2026), and the resulting naming confusion.
2. AG2 communication primitives WITH CODE: ConversableAgent.initiate_chat, register_reply, GroupChat + GroupChatManager (speaker-selection: auto/round_robin/manual/random/custom), nested chats, sequential chats, and especially the SWARM pattern with handoffs (register_hand_off, AfterWork, OnCondition) — how an agent autonomously transfers control to another agent.
3. Microsoft AutoGen 0.4+ Core runtime: the actor model, agent runtime, topics & subscriptions, publish/subscribe messaging, RoutedAgent, @message_handler, and how an agent publishes to a topic that subscribed agents receive UNPROMPTED. Then the AgentChat layer on top: RoundRobinGroupChat, SelectorGroupChat, Swarm, Magentic-One, GraphFlow.
4. How an agent asks for clarification / requests more information / escalates back to the orchestrator or to a human: UserProxyAgent, human_input_mode (ALWAYS/TERMINATE/NEVER), termination conditions, HandoffMessage to user.
5. Inter-agent decision-making / consensus: speaker selection, manager-mediated turn-taking, voting patterns.
6. Concrete, current code examples for each mechanism. Cite official docs (microsoft.github.io/autogen, docs.ag2.ai) and note version-specific differences. End with a comparison table of AG2 vs Microsoft AutoGen 0.4 for agent-to-agent messaging.
**Completed:** 2026-05-28T09:30:05.971340
**Duration:** 4.1 minutes
**Cost:** $0.0
**Model:** deep-research-pro-preview-12-2025

---


## Metadata

| Field | Value |
|-------|-------|
| Task ID | `1fdfc4e5-ef6b-45ee-bd4b-4755277341b9` |
| Status | completed |
| Created | 2026-05-28T09:26:00.032312 |
| Completed | 2026-05-28T09:30:05.971340 |
| Duration | 4.1 minutes |
| Tokens (Input) | 0 |
| Tokens (Output) | 0 |
| Estimated Cost | $0.0000 |
| Model | deep-research-pro-preview-12-2025 |


---

## Research Report

# Deep Technical Research: Autonomous Agent Communication in Microsoft AutoGen (v0.4+) versus AG2

**Key Points:**
*   **The Fork:** In November 2024, the original creators of AutoGen parted ways with Microsoft, creating a community-driven fork named AG2 (continuing the v0.2.x lineage). Simultaneously, Microsoft completely rewrote AutoGen for v0.4+, adopting an event-driven, actor-model architecture [cite: 1, 2]. 
*   **Architectural Divergence:** AG2 relies on a **Conversational Model** where agents are participants in a centralized `GroupChat` orchestrated by a `GroupChatManager` [cite: 3, 4]. In contrast, Microsoft AutoGen v0.4 utilizes an **Event-Driven Actor Model** (`autogen-core`) where agents run asynchronously and communicate via a Publish/Subscribe (Pub/Sub) messaging bus [cite: 5, 6].
*   **Unprompted Communication:** True "unprompted" or "agent-initiated" communication is natively realized in Microsoft AutoGen v0.4 through the `publish_message` method and `TopicId` subscriptions [cite: 5, 7]. An agent can autonomously broadcast a message to a topic at any time, and any subscribed agent will receive it instantly via a `@message_handler` without central orchestration. In AG2, unprompted communication is simulated via specific speaker-selection policies or conditional Swarm handoffs (`OnCondition`) triggered by LLM tool calls [cite: 8, 9].
*   **Human-in-the-Loop:** Both frameworks maintain robust human escalation paths. AG2 utilizes the `UserProxyAgent` with `human_input_mode` (ALWAYS, TERMINATE, NEVER) [cite: 10]. Microsoft AutoGen v0.4 implements structured `HandoffMessage` protocols allowing any agent to yield execution back to a human user [cite: 11, 12].
*   **Advanced Orchestration:** Microsoft v0.4 introduces `Magentic-One` (a generalist architecture with an Orchestrator, Task Ledger, and specialized workers) and `GraphFlow` (DiGraph-based deterministic state machines) [cite: 13, 14]. AG2 focuses on enhancing its `Swarm` paradigm with refined context variable tracking and nested chats [cite: 9, 15].

---

## 1. The AutoGen Ecosystem Split: Genesis and Architecture Divergence

The landscape of multi-agent orchestration experienced a seismic shift in late 2024. To understand the current technical paradigms of agent-to-agent communication, one must first understand the historical and architectural bifurcation of the AutoGen framework.

### 1.1 Timeline and Rationale for the Fork
AutoGen was initially developed by researchers Chi Wang and Qingyun Wu inside the FLAML (Fast Library for Automated Machine Learning) open-source project [cite: 2]. It was spun off as a standalone repository in October 2023 by Microsoft Research, quickly becoming one of the most popular frameworks for building multi-agent systems driven by Large Language Models (LLMs) [cite: 1, 2, 16].

However, divergent visions regarding governance, architecture, and commercialization led to a profound split. In November 2024, the founding team (including Chi Wang, who transitioned to Google DeepMind, and researchers from Penn State University) established an independent, community-driven organization (ag2ai) and branched out the code [cite: 2, 17, 18]. They rebranded their continuation of the framework as **AG2** [cite: 1, 3]. The stated goal of the AG2 fork was to "enable open-governance from different organizations" and facilitate community-led growth, ensuring backward compatibility with the highly successful AutoGen v0.2.x API [cite: 1, 2]. 

Concurrently, Microsoft maintained control of the official `microsoft/autogen` repository and executed a complete, ground-up rewrite of the framework, resulting in **Microsoft AutoGen v0.4+** [cite: 1, 19]. Microsoft's goal was to transition from a rigid conversational paradigm to a highly scalable, distributed, and enterprise-ready event-driven architecture, heavily integrating concepts from the Actor Model and aligning with Microsoft's broader AI ecosystem, including Semantic Kernel [cite: 1, 16].

### 1.2 Package Nomenclature and Version Confusion (2025-2026)
This split has generated significant friction and naming confusion in the developer community [cite: 20]. 
*   **AG2 Packages:** The AG2 team retained control over the original PyPI packages. Thus, installing `autogen`, `pyautogen`, or `ag2` via pip currently installs the AG2 framework (v0.3.x / v0.4.x / v0.9.x community builds) [cite: 1, 15, 20]. AG2 remains fully backward compatible with legacy AutoGen v0.2 codebases [cite: 1, 17].
*   **Microsoft AutoGen 0.4+ Packages:** Microsoft distributes its newly rewritten framework through entirely new PyPI namespaces: `autogen-core` (the low-level event-driven runtime), `autogen-agentchat` (the high-level API), and `autogen-ext` (extensions) [cite: 1, 6]. The v0.4 architecture is fundamentally incompatible with v0.2/AG2 code [cite: 1].

### 1.3 Core Architectural Philosophies
The fundamental difference between the two frameworks dictates how agents communicate autonomously:
1.  **AG2 (Conversational Turn-Taking):** Models agent systems strictly as a group conversation [cite: 3]. Agents do not technically "call" each other peer-to-peer; rather, they submit messages to a central orchestrator (`GroupChatManager`), which appends the message to a shared history and executes a policy to determine who speaks next [cite: 4, 21].
2.  **Microsoft AutoGen 0.4 (Event-Driven Actor Model):** Models agents as independent, stateful asynchronous actors (`RoutedAgent`). Agents communicate exclusively through message passing over a distributed Pub/Sub (Publish-Subscribe) event bus (`SingleThreadedAgentRuntime`) [cite: 5, 6, 22]. There is no requirement for a central orchestrator; an agent can autonomously publish an event to a topic, instantly waking up any subscribed agent [cite: 7].

---

## 2. AG2 Communication Primitives: The Conversational Paradigm

In AG2, agent-initiated communication is handled through conversational structures. While an agent cannot strictly "push" an asynchronous background task to another agent, it *can* autonomously decide to hand off control, request information, or alter the execution flow during its designated turn.

### 2.1 Base Primitives: `ConversableAgent` and `initiate_chat`
The foundation of AG2 is the `ConversableAgent`. This class wraps an LLM, tools, and memory into an entity capable of generating replies [cite: 23]. Communication is fundamentally synchronous and blocking.
An interaction is typically kicked off using `initiate_chat()`:
```python
from autogen import ConversableAgent, UserProxyAgent

agent_a = ConversableAgent(name="Analyzer", llm_config=...)
agent_b = ConversableAgent(name="Summarizer", llm_config=...)

# Initiates a direct 1-to-1 blocking chat
agent_a.initiate_chat(agent_b, message="Analyze this dataset.")
```
Under the hood, AG2 utilizes a method called `register_reply` to build a chain of responsibility for processing incoming messages (e.g., checking for tool calls, querying the LLM, or asking for human input) [cite: 24]. 

### 2.2 Orchestrated Communication: `GroupChat` and `GroupChatManager`
For multi-agent systems, AG2 uses the `GroupChat` and `GroupChatManager` classes [cite: 4]. This is akin to a Slack channel rather than Direct Messaging [cite: 4]. The `GroupChatManager` acts as the central router [cite: 21].

#### Inter-Agent Decision Making: Speaker Selection
When an agent finishes generating a message, the `GroupChatManager` broadcasts the message to all participants' contexts [cite: 21]. The manager then must decide *who speaks next*. This is where autonomous inter-agent communication is simulated. AG2 provides several `speaker_selection_method` policies [cite: 25]:
*   **`round_robin`:** Deterministic, sequential turn-taking [cite: 25, 26].
*   **`random`:** Selects the next speaker randomly [cite: 25].
*   **`manual`:** Halts execution and awaits human selection [cite: 25].
*   **`auto` (LLM-based):** The true engine of autonomous interaction. The `GroupChatManager` uses its own LLM to read the conversation history and dynamically select the next logical speaker [cite: 4, 26]. It uses a specific prompt template (`select_speaker_prompt_template`) asking the LLM: *"Read the above conversation. Then select the next role from {agentlist} to play. Only return the role."* [cite: 25]. 
*   **Custom Function:** Developers can write a Python function that takes the `last_speaker` and the `groupchat` state, and deterministically routes the conversation [cite: 27].

**Code Example: Custom Speaker Selection in AG2**
```python
import autogen
from autogen import GroupChat, GroupChatManager

def custom_routing(last_speaker, groupchat):
    # If the researcher finishes, the writer MUST autonomously take over
    if last_speaker.name == "Researcher":
        return groupchat.agent_by_name("Writer")
    elif last_speaker.name == "Writer":
        # Simulate an autonomous request for human review
        return "manual" 
    return "auto"

groupchat = GroupChat(
    agents=[researcher, writer, reviewer],
    messages=[],
    max_round=10,
    speaker_selection_method=custom_routing # Custom autonomous routing
)

manager = GroupChatManager(groupchat=groupchat, llm_config=llm_config)
user_proxy.initiate_chat(manager, message="Start the research process.")
```

### 2.3 The SWARM Pattern: Autonomous Handoffs
The most sophisticated mechanism for *agent-initiated* communication in AG2 is the **Swarm** pattern. Borrowing concepts from OpenAI's experimental Swarm framework, AG2 allows agents to dynamically transfer control based on their capabilities and current context [cite: 11, 28, 29]. Instead of relying on the `GroupChatManager`'s LLM to guess who should speak next, an agent explicitly executes a handoff tool [cite: 11, 29].

In modern AG2 (v0.9+), the Swarm orchestration has been merged deeply into the new group chat functionality, deprecating older methods like `initiate_swarm_chat` [cite: 9]. Agents manage handoffs directly using `OnCondition` and `AfterWork` classes [cite: 9].

*   **`OnCondition`:** An LLM-driven conditional handoff. The agent is given a tool description (e.g., "If you need weather data, hand off to the Weather_Agent"). The LLM decides autonomously when to trigger this tool [cite: 8].
*   **`OnContextCondition`:** Handoffs triggered automatically based on the state of global shared memory variables [cite: 9].
*   **`AfterWorkOption`:** Defines the fallback action if no tool calls are made (e.g., `TERMINATE`, `REVERT_TO_USER`, `STAY`) [cite: 8, 15, 30].

**Code Example: AG2 Swarm Handoff (v0.9+ syntax)**
```python
from autogen.agentchat.contrib.swarm_agent import (
    AfterWorkOption, 
    OnCondition, 
    register_hand_off
)
from autogen import ConversableAgent

# 1. Define Agents
triage_agent = ConversableAgent(name="Triage", llm_config=llm_config, system_message="You classify requests.")
flight_agent = ConversableAgent(name="Flights", llm_config=llm_config, system_message="You handle flight cancellations.")
billing_agent = ConversableAgent(name="Billing", llm_config=llm_config, system_message="You process refunds.")

# 2. Register Autonomous Handoffs (Agent-Initiated Communication)
register_hand_off(
    agent=triage_agent,
    hand_to=[
        OnCondition(flight_agent, "User wants to cancel or modify a flight."),
        OnCondition(billing_agent, "User has questions about a charge or refund.")
    ]
)

register_hand_off(
    agent=flight_agent,
    hand_to=[
        # Flight agent autonomously transfers to billing once cancellation is confirmed
        OnCondition(billing_agent, "Flight cancelled. Proceed to refund.")
    ],
    # Fallback to human when done
    after_work=AfterWorkOption.REVERT_TO_USER 
)
```
In this paradigm, the agent actively *chooses* to communicate with another agent by generating a specific tool-call token [cite: 29]. The framework intercepts this tool call and physically transfers the execution context to the recipient agent [cite: 29, 31].

---

## 3. Microsoft AutoGen 0.4+ Core Runtime: The Event-Driven Actor Model

While AG2 relies on conversational loops, **Microsoft AutoGen 0.4** introduces `autogen-core`, a low-level framework built on the **Actor Model** [cite: 6, 22]. In an Actor Model, each agent (actor) operates independently, encapsulates its own state, and communicates with other agents exclusively through asynchronous message passing [cite: 22].

This represents a profound shift. Agents are no longer just instances of an LLM wrapped in a Python loop; they are background processes managed by an **Agent Runtime** [cite: 32].

### 3.1 The Agent Runtime and `RoutedAgent`
The `SingleThreadedAgentRuntime` (or distributed equivalents) provides the execution environment [cite: 32]. It manages lifecycles, enforces security, and handles message routing [cite: 32]. 
Developers build agents by subclassing `RoutedAgent`. Instead of overriding generic `on_message` loops, developers define strictly typed data classes for messages and use the `@message_handler` decorator [cite: 5, 33].

```python
from dataclasses import dataclass
from autogen_core import RoutedAgent, MessageContext, message_handler

@dataclass
class DataProcessingRequest:
    dataset_url: str
    priority: str

class DataProcessorAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("DataProcessor")

    # The agent autonomously wakes up whenever it receives this specific message type
    @message_handler
    async def process_data(self, message: DataProcessingRequest, ctx: MessageContext) -> None:
        print(f"[{self.id.type}] Received request for {message.dataset_url}")
        # LLM logic goes here...
```

### 3.2 UNPROMPTED COMMUNICATION: Pub/Sub, Topics, and Subscriptions
This is the crux of the user's query regarding **agent-initiated / unprompted communication**. AutoGen 0.4 handles unprompted communication flawlessly through its **Publish-Subscribe (Pub/Sub) API** [cite: 5, 7, 34].

In traditional Point-to-Point messaging (like AG2's `initiate_chat` or a direct RPC call), the sender must know the exact ID of the receiver [cite: 34, 35]. In Pub/Sub, this coupling is eliminated [cite: 36, 37]. Agents broadcast messages to a **Topic** [cite: 7, 35]. Any agent that has registered a **Subscription** to that topic will automatically receive the message [cite: 35].

#### Mechanism of Unprompted Broadcast
1.  **Topics (`TopicId`):** A topic consists of a `type` (e.g., `"system_events"`) and a `source` (e.g., `"default"` or a specific GitHub repo ID) [cite: 7, 38].
2.  **Publishing (`publish_message`):** An agent can autonomously call `self.publish_message(message, topic_id)` at any point in its execution [cite: 5]. This is a fire-and-forget broadcast. The publishing agent expects no immediate return value [cite: 5].
3.  **Subscriptions (`TypeSubscription`):** Other agents declare their interest in topics [cite: 7]. The runtime ensures that whenever a message hits the topic, the subscribed agent's `@message_handler` is invoked asynchronously, entirely unprompted by a central orchestrator [cite: 5].

**Concrete Code Example: Unprompted Pub/Sub Communication (AutoGen 0.4 Core)**
```python
import asyncio
from dataclasses import dataclass
from autogen_core import (
    RoutedAgent, MessageContext, SingleThreadedAgentRuntime, 
    message_handler, TopicId, TypeSubscription, default_subscription
)

@dataclass
class AnomalyDetectedEvent:
    description: str
    severity: int

# 1. The Monitoring Agent (Publisher)
class MonitorAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("Monitor")

    async def run_continuous_scan(self):
        # ... simulating some background monitoring task ...
        anomaly_found = True
        if anomaly_found:
            # UNPROMPTED COMMUNICATION: The agent decides to broadcast an event
            print("[Monitor] Anomaly detected! Publishing event...")
            await self.publish_message(
                message=AnomalyDetectedEvent(description="CPU Spiking", severity=9),
                topic_id=TopicId(type="alerts", source="server_1")
            )

# 2. The Responder Agent (Subscriber)
# The decorator automatically subscribes instances of this agent to the "alerts" topic
@default_subscription # Simplifies subscription to the default topic scope
class ResponderAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("Responder")

    # Wakes up UNPROMPTED when a message hits the subscribed topic
    @message_handler
    async def handle_anomaly(self, message: AnomalyDetectedEvent, ctx: MessageContext) -> None:
        if message.severity > 8:
            print(f"[Responder] CRITICAL ACTION REQUIRED: {message.description}. Initiating mitigation.")
        else:
            print(f"[Responder] Logging minor anomaly: {message.description}.")

async def main():
    runtime = SingleThreadedAgentRuntime()
    
    # Register agents to the runtime
    await MonitorAgent.register(runtime, "monitor", lambda: MonitorAgent())
    await ResponderAgent.register(runtime, "responder", lambda: ResponderAgent())
    
    # Explicitly map the topic type "alerts" to the "responder" agent type
    await runtime.add_subscription(TypeSubscription(topic_type="alerts", agent_type="responder"))
    
    runtime.start()
    
    # Trigger the monitor to start its independent loop
    monitor = await runtime.try_get_underlying_agent_instance(MonitorAgent.id)
    await monitor.run_continuous_scan()
    
    await runtime.stop_when_idle()

if __name__ == "__main__":
    asyncio.run(main())
```
*Analysis of Unprompted Action:* The `ResponderAgent` did not have a "turn" in a conversation. It was completely dormant until the `MonitorAgent` published the `AnomalyDetectedEvent`. This decoupling allows infinite scalability; multiple different security, logging, and UI agents could all subscribe to the `"alerts"` topic and react simultaneously to the single broadcast [cite: 5, 7, 36].

---

## 4. Microsoft AutoGen 0.4+ AgentChat Layer: Advanced Orchestration

While `autogen-core` handles low-level Pub/Sub, developers building LLM applications typically use the `autogen-agentchat` high-level API. This layer provides familiar constructs (Teams and GroupChats) but reconstructs them entirely on top of the robust core runtime [cite: 6, 39].

### 4.1 Basic Teams: `RoundRobinGroupChat`, `SelectorGroupChat`, and `Swarm`
Microsoft AutoGen 0.4 offers Team abstractions that map loosely to the older AG2 concepts but execute via the underlying event bus [cite: 11, 14].
*   **`RoundRobinGroupChat`:** Sequential execution [cite: 40].
*   **`SelectorGroupChat`:** Uses an LLM to dynamically determine the next speaker based on context history. This replaces AG2's `GroupChatManager` with `speaker_selection_method="auto"` [cite: 40, 41].
*   **`Swarm`:** Implements the Swarm design pattern natively. Instead of AG2's `register_hand_off`, Microsoft's Swarm allows `AssistantAgent` to specify a `handoffs` argument. The agent uses LLM tool-calling to generate a `HandoffMessage`, transferring context and control dynamically to another agent [cite: 11]. 

### 4.2 Magentic-One: The Generalist Architecture
One of the most significant additions to Microsoft AutoGen v0.4 is **Magentic-One**, a high-performing generalist multi-agent system designed to solve complex, open-ended web and file-based tasks [cite: 13, 39, 42]. 

Magentic-One relies on strict hierarchical orchestration rather than decentralized Pub/Sub:
1.  **The Orchestrator:** The lead agent. It features a unique dual-loop architecture (an inner loop for execution, an outer loop for planning) [cite: 13]. 
2.  **Task Ledger & Progress Ledger:** The Orchestrator maintains shared memory structures. The Task Ledger holds strategic plans and facts, while the Progress Ledger tracks real-time execution status [cite: 13, 43].
3.  **Specialized Workers:** The Orchestrator delegates subtasks to highly specialized agents, including `WebSurfer` (browser control), `FileSurfer` (local filesystem), `Coder` (code generation), and `ComputerTerminal` (code execution) [cite: 33, 39, 41].

In Magentic-One, agent communication is *manager-mediated*. A worker (e.g., `Coder`) cannot independently command the `WebSurfer`. The Coder completes its task, returns control to the Orchestrator, which updates the Progress Ledger and subsequently dispatches the `WebSurfer` [cite: 13, 43].

### 4.3 GraphFlow: Deterministic State Machines
For production workflows requiring absolute strictness, AutoGen 0.4 introduces **GraphFlow** [cite: 14]. GraphFlow treats agent execution as a directed graph (`DiGraph`) [cite: 14].
*   **Execution Graph vs. Message Graph:** Crucially, GraphFlow separates execution control from data visibility [cite: 14]. The Execution Graph dictates the strict sequence of which agent operates next (handling parallel branches, conditional loops, and joins). The Message Graph utilizes message filtering (`MessageFilterAgent`) to restrict which parts of the conversation history each agent is allowed to see, effectively reducing context bloat and LLM hallucinations [cite: 14]. GraphFlow is utilized when conversational non-determinism is unacceptable [cite: 14].

---

## 5. Human Escalation and Feedback Mechanisms

In fully autonomous systems, agents inevitably encounter ambiguity, missing data, or high-risk actions requiring human authorization. Both frameworks handle clarification, information requests, and human handoffs extensively.

### 5.1 AG2: The `UserProxyAgent` and `human_input_mode`
AG2 manages human intervention via the `UserProxyAgent`, a specialized `ConversableAgent` designed to halt execution and poll the terminal (or a frontend) for human text input [cite: 10, 24].

The core parameter controlling this behavior is `human_input_mode` [cite: 10, 24, 44, 45]:
*   **`ALWAYS`:** The agent pauses and prompts the human *every single time* a message is received [cite: 10, 46, 47]. The human can provide feedback (steering the LLM), type "exit" to terminate, or press Enter to skip and let the LLM auto-reply [cite: 46]. This is ideal for high-stakes tasks like executing unverified Python code [cite: 45].
*   **`TERMINATE` (Default):** The agent operates autonomously until a specific termination condition is met (e.g., an LLM outputs the word "TERMINATE" or the conversation hits `max_consecutive_auto_reply`) [cite: 10, 45]. At that point, it asks the human if they want to interject or officially end the process [cite: 46, 47].
*   **`NEVER`:** Fully autonomous. The agent will *never* prompt the human. It runs until completion or an error occurs [cite: 10, 46, 47].

**Code Example: AG2 Human-in-the-Loop Escalation**
```python
from autogen import AssistantAgent, UserProxyAgent

# The AI worker
assistant = AssistantAgent(
    name="Coder",
    llm_config=llm_config
)

# The human proxy with strict authorization
user_proxy = UserProxyAgent(
    name="Human_Supervisor",
    human_input_mode="TERMINATE", # Will run autonomously until task completion
    max_consecutive_auto_reply=10, # Failsafe to prevent infinite loops
    is_termination_msg=lambda x: "TERMINATE" in str(x.get("content", "")).upper(),
    code_execution_config={"work_dir": "workspace", "use_docker": True}
)

user_proxy.initiate_chat(assistant, message="Write a python script to scrape data.")
```

### 5.2 Microsoft AutoGen 0.4: `HandoffMessage`
In the v0.4 AgentChat layer, escalating back to a human is treated functionally identically to handing off to another AI agent, streamlining the architecture [cite: 11].

When building a `Swarm` or utilizing the `task` APIs, the framework exposes a `HandoffMessage` class [cite: 11, 12]. If an agent requires human clarification, it uses its tool-calling capabilities to generate a `HandoffMessage` targeting the `"user"` [cite: 11].
When this occurs, the runtime execution halts and yields. The backend (like a FastAPI web server or terminal) collects the user's input and injects it back into the stream as a resumption payload [cite: 11, 12].

**Code Example: AutoGen 0.4 User Handoff**
```python
import asyncio
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import Swarm
from autogen_agentchat.task import HandoffTermination, Console
from autogen_agentchat.messages import HandoffMessage

# Agent is explicitly given the capability to handoff to the "user"
agent = AssistantAgent(
    "SupportAgent",
    model_client=model_client,
    handoffs=["user"], 
    system_message="You answer support queries. If you need account details, ask the user."
)

# Terminate execution when a handoff to the user occurs
termination = HandoffTermination(target="user")
team = Swarm([agent], termination_condition=termination)

async def run():
    # 1. Start task
    await Console(team.run_stream(task="I want to delete my account."))
    
    # Execution pauses here. The agent realizes it needs confirmation and hands off to "user".
    # 2. Simulate User providing the requested feedback:
    print("\n--- Awaiting User Input ---\n")
    user_input = "Yes, my account ID is 12345."
    
    # 3. Resume the swarm by pushing the HandoffMessage back in
    await Console(
        team.run_stream(
            task=HandoffMessage(source="user", target="SupportAgent", content=user_input)
        )
    )

asyncio.run(run())
```

---

## 6. Inter-Agent Decision-Making and Consensus

How do agents fundamentally decide who does what, and when? The methodology differs based on the orchestration pattern chosen.

### 6.1 Manager-Mediated Turn-Taking (AG2 GroupChat & MS SelectorGroupChat)
In both AG2's `GroupChatManager` (with `speaker_selection_method="auto"`) and MS v0.4's `SelectorGroupChat`, decision-making relies entirely on the reasoning capabilities of an LLM [cite: 4, 25, 41].
*   **The Context Window:** The Selector LLM is injected with the `system_message` of every available agent, as well as the full recent conversation history [cite: 21, 25].
*   **The Prompt:** The LLM is given a meta-prompt (e.g., `select_speaker_prompt_template`) instructing it to act as an impartial judge and output *only* the name of the next agent [cite: 25, 41].
*   **The Loop:** Agent A speaks -> Selector LLM reads output -> Selector LLM outputs "Agent B" -> Agent B speaks [cite: 21, 41].
*   **Pros & Cons:** Highly dynamic and adaptable to unpredictable conversation flows [cite: 41]. However, it is token-intensive (requires an extra LLM call between every single agent turn) and prone to hallucinations or infinite looping if the LLM gets confused [cite: 3, 4].

### 6.2 Autonomous Tool-Based Handoffs (Swarm Patterns)
In the Swarm pattern (both AG2 and MS v0.4), the central Selector LLM is eliminated [cite: 11, 29]. Decision-making is distributed. 
The agent currently "holding the mic" assesses its own state [cite: 11]. If it realizes it needs a different skill set (e.g., the Triage Agent realizes the user wants a refund), it executes a native function/tool call (e.g., `transfer_to_billing()`) [cite: 29, 31]. 
This is fundamentally more reliable than `SelectorGroupChat` because LLMs are heavily fine-tuned for accurate JSON tool-calling, significantly reducing routing hallucinations [cite: 29].

### 6.3 Programmatic Voting and Graph Logic
For rigorous consensus, raw conversational models fall short. 
*   **AG2:** Developers must write custom Python logic inside the `custom_speaker_selection_func`. For example, tracking the outputs of three `CriticAgents`, parsing their numerical scores, and yielding to a human if the average score falls below a threshold [cite: 4, 27].
*   **Microsoft AutoGen 0.4:** Consensus and logic branching are handled natively by `GraphFlow` [cite: 14]. Nodes in the directed graph can define boolean conditions based on agent outputs, deterministically routing execution down successful or fallback branches without relying on LLM intuition [cite: 14].

---

## 7. Comprehensive Comparison Table: AG2 vs. Microsoft AutoGen 0.4+

The following table summarizes the technical realities of agent-to-agent communication across the two distinct lineages in 2025-2026.

| Feature / Architecture | AG2 (Community Fork, formerly AutoGen 0.2.x) [cite: 1, 3] | Microsoft AutoGen (v0.4+ Rewrite) [cite: 1, 6] |
| :--- | :--- | :--- |
| **Maintainer & Ethos** | Open community (ag2ai), original creators. Focuses on stability and proven conversational paradigms [cite: 1, 2, 17, 20]. | Microsoft Research. Focuses on enterprise scalability, distributed systems, and integration (Semantic Kernel) [cite: 1, 6, 16, 19]. |
| **Primary Packages** | `ag2`, `autogen`, `pyautogen` [cite: 1, 15, 20] | `autogen-core`, `autogen-agentchat`, `autogen-ext` [cite: 1, 6, 20] |
| **Underlying Architecture** | Conversational loop. Agents are instantiated objects running in a blocking Python process [cite: 3, 4]. | Event-driven Actor Model. Agents are asynchronous actors running in a managed runtime [cite: 5, 22, 32]. |
| **UNPROMPTED Communication** | **Simulated / Orchestrated:** Achieved via LLM speaker selection or explicit tool-based Swarm handoffs (`OnCondition`) [cite: 4, 8, 9]. | **Native / Asynchronous:** Achieved via Distributed Pub/Sub. Agents use `publish_message` to `TopicId`, waking subscribed agents unprompted [cite: 5, 7]. |
| **Message Routing Layer** | `GroupChatManager` acts as a central router, evaluating `speaker_selection_method` (`auto`, `round_robin`, `custom`) [cite: 4, 21, 25]. | Event bus routes messages by strictly typed Python `dataclass` types to `@message_handler` functions [cite: 5, 33]. |
| **Swarm / Handoff Logic** | `register_hand_off`, `OnCondition` (LLM-evaluated), `OnContextCondition` (state-evaluated), `AfterWorkOption` [cite: 8, 9, 30]. | Agents specify `handoffs=["AgentB"]`. LLM generates a `HandoffMessage` transitioning control [cite: 11, 12]. |
| **Human Escalation** | `UserProxyAgent` utilizing `human_input_mode` (ALWAYS, TERMINATE, NEVER) [cite: 10, 24, 45, 46]. | Standard `AssistantAgent` issues a `HandoffMessage` targeted to `"user"`, triggering runtime suspension [cite: 11, 12]. |
| **Deterministic Workflows** | Custom Python functions passed to `speaker_selection_method`, or manually linking sequential chats [cite: 23, 27]. | Native `GraphFlow` teams utilizing directed graph (`DiGraph`) state machines with separated Message Graphs [cite: 14]. |
| **Generalist Meta-System** | N/A - Relies on developer-built custom GroupChats. | `Magentic-One` built-in: Orchestrator, Task/Progress Ledgers, and pre-configured multimodal Surfers/Coders [cite: 13, 39, 43]. |
| **State Persistence** | Appended lists of dictionaries (Message History). Often challenging to resume natively if interrupted without custom wrappers [cite: 3, 4]. | Core runtime natively supports saving and loading Actor states and event logs, enabling durable, long-running processes [cite: 4, 22, 32]. |

### Conclusion
The divergence of AutoGen into AG2 and Microsoft AutoGen 0.4 represents a classic software engineering evolution: moving from a highly successful, monolithic prototype to specialized architectures. For developers aiming to build rapid, conversational, "chatbot-style" multi-agent networks, **AG2** remains the most ergonomic and battle-tested choice [cite: 3, 19]. Its `GroupChatManager` and `Swarm` handoffs perfectly simulate autonomous communication within a controlled loop [cite: 4, 9].

However, for deep technical deployments requiring genuine, asynchronous, **agent-initiated/unprompted communication**, **Microsoft AutoGen 0.4** is mathematically and architecturally superior [cite: 6, 14, 22]. By implementing the Actor Model and a robust Pub/Sub event bus with topics and subscriptions, agents are freed from the constraints of linear turn-taking, allowing them to operate as true independent software daemons reacting to and emitting events across a distributed network [cite: 5, 7].

**Sources:**
1. [dev.to](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEZU8910QCVBrSef0Aa8Yu_TgBkTeBw5dtJkZ10ZFsV-Ug2CpI-5znlZ6Hyhz2c099QCOTGN_pgTdGJGOK3X5Gs2KUmguzLU3ITcEMwPGGpT6JMbOPfba_Kd2mr2X0PltZ2W6rmort6cHI-0IVgN235KFtsPEN7MnZ5eKl9aR-xeszjYf8g90Wv)
2. [github.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQH7X3iabYx34H5xeFSH8hhmcBvkI25p1zSQbO4o1nV_I4sgTifTdpaqj5E4o9pUa5F3bz49BAhj0W8DZIaMCb_A0MEuEsKgnDBssJCmNhUu2452neuN40R8LhqtF5f6UzEA0SQO1d2boBVumw==)
3. [gitconnected.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQGVbKp-NPz_y0RGodnuPUOE6HpDArQ3OGkQBqf53auik4sfMlmYmtG5JeHHK1rIFBIb5_kLwPnBKGo3bypWq-AhWSkJK9u2wYFg37QlCZ2tbFkF7KUdAzthogZEK5zM1mYukJi1jKpQotxppG-5NYAWCqR1RTo3ND8HfWHf_A9SKSe9fWMxHWIIi3yeGviGSFBSWwR1Co_KOFVP565Cip0v_swxuekj59CgbwOhF3OqtVmggbVx-lP7B6TWx1eUKbqJ2cZYoC-JE5hEVX3269veep2V2p_jwk2vNO-lXQ==)
4. [substack.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFeiLcSptZ1Kv46KpuN6h3a8iYLcs_jdC_D3n6t1hbZ4bmbr4I_NEK5DwYtpAGzTYP6-Mz95RoIwALvv-RIDMr4qpvcBs5k6blVAhN7IH6XOmSCAMWtfw8tHTHkOmuZJlgLzp0Q0FMs8T3EAA20ja8cVz15zJ9rhKEe0LeSh5-w)
5. [github.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQGhp7Lsur4TPMyzv52WNbs8EkiBiNugWN-W_abvQV1EfSdN5mwaeEtix6_rfMKmqaPYqMkhPhrVI9mVrFH5q0Uh59r8K2xsWeKFyHORDRo4fEqHcCZTttbLHIabnoOfS-CHA0qERnTbaTMxcg_atDqsqR_3mzaEi38S0OREFELp5PggE2d7HV6VRmww6yUlEt1eaIHd2JSou7QZj9JlkhxrcHJAQg==)
6. [microsoft.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHmVyTIrPBTvpwcFxYPMUrYaAag5uo45EVZBejqbQPCg8cvGhZarPzrggxXvKcHH8H2NTfAYMotMZcxl5Gv4VC3AEQUHqlEqKIp2hQIDkizr1W-KpWY6C5WbqwuXcVkFfeCHK0Jv1wLMJ7sOd1fNODPSYE-nraz0sUvlgDuCbaUltxd1cGE)
7. [github.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEEENQeQO7EnuuKDoiShoPAnpUP2xYS_Inq3iUr3p13XrnXT51l0GSTmXbL0R9X7HwT12i4TdrCwcWpV7E6olhG-acycNouiz2fYgOcI9ApUbDiXJUgxtV64fW5v7XfkN1bYoIS9Zdb_o264P_Ke_M8sq0zvf2FSUtMfsUMuVOOpW1qcRGdeBhe51ZBOwBZ-xQgXA-F7lXoQ8A8gtTMT4B-EIAd8GOW)
8. [ag2.ai](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFxASe7NNN2-1oMSFnjOoZKhtBVXiZs0KuLkU_oFew2MndNFpLYKP5eZnN-VUJQkw8X3Wn2S4zKZldcWEaPuF9spV2emk8YvfRvUfBcfgoiPeUw076dAnJtVkdzO4cRn9a_f-9Ue11XzvdulFY=)
9. [ag2.ai](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQE9OelP68mI0eHWxctmE7r70qFRtwISMc_G_BRfOS264I1lJ_86mWOMLXdPXlWJXYC7HfOzS5KsJI4gdhmPZT_2Ve1W4dK8DqFvC-zNBD1vFbdPtlYy2_2VHi3aHaFTfYeloMtvvu6chyU-XZhdUFagOY0Vb4VQkg_UuPo_CSJTwRXMsuV3uOq4YFSewu3MAwmpmfw=)
10. [ag2.ai](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEwq8FMjSlyO64EVnz8kLXbyzJrcLKJr3ommQoTz8ixZXclZilaOE-gys078BgnQeXAe2DT8q2_BI-26P6kjEwOaP2UeXHHzV7jfP36Hr6AMufm5Jp1p0DdujG9-alxFa3cgqVZX7HLgy58mgq7yv1Y-xjzpMnKk6gzsfY=)
11. [github.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHv-xiGFgWLV6YI18vjd8C3D1rA5_mYeqANOyRkkSohpLjUGGzz67B7AD3gs3PdvvjwONZHMzXBPQ5AyeaRn4nHQZxWc6Uve1G4PNjOecjNvnKNzx4z2mOQOIKgugQeBTHWwiUIwaobXagl14J9yudaGDhLKUCFDZ9-_j9JSNTlbUeBt7kRnidWsA==)
12. [reddit.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFsh-03wAr6RvMBW4WW1xskUnRca9o2LakLTOfI6WXkuVAmJghP541doLydzJVKul4NTQGB7kK9HdXUjsAqSbs-N2Gsb36fNH3Fsm_HOAcmxRnc1CFkEIsQXrtgUddWhaOuvBrfkJZPCLxaLbI7U80QC8UFOaR8zV3IXhcOz6qzUNAzLQvnqpJwaTPrV9klo0tD)
13. [microsoft.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHeOwy0Hqmjnj7PVURujWqQmfpENpO-slZJRdwwej9VTXriK0eNuY8luXdPaD3wSgJiGTTi3k2Jj7ElU9emebBMh47mZXapCQS_Hziadf336fR4rWbBYRNomtVHkpCVVgVxkKzRPgeRYjyU8dX-mnfr9gMGgXoy5uds8t7q1VumbB8Ho1quLPyoJcvcQI2cWhZKi4IEQQ9A-1hxCQoOq8WKkSp30rbD8b9ADFgMIVQ2)
14. [github.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFrQ5fmX0YPF3V72FjfAGLpEab4t3fH_y6p4Mvn_EyhPr_TvaUOd_FtW5-5D7chf3zTlwJdU96Q1hZvdvPb6OcnB4U0v59XEi9zgVsMjk5kebbTpFLZccl6xeGpwjQ46OPTHLbIyLYjWtUh6t_cxVX60biKoHQRvpoTME3tLQJDQS72h5k2kenC3wMOLY8ljYkL)
15. [ag2.ai](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQE8CRrfaHe2w4GaTPoY4osncXCen_WPE5T95BIBNIJTIU9sZ2iA5QDRB7FtXk_AnBlJpDQl9Nr9pXBzojvBeQOK5ffC_mukVcOnVO_e4wPinmWpBKqAOkN2QxiJ4w5hJ8Qx0F0iqib_q01drMFcua-3Erumv5EbeBqvvwhmeYoFILRiXrGI8VbtmAtghJE=)
16. [juekong-research.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHmpVxjJyjjpOTmY22w6CLteposAz2v3gGNmHdgBRoey2oGLc_3-B38C7uQa_zZB8TtbVKw58PaLVOgHkNYnd-BpjzfSDT1HzqCJwV_JyZVdrQu_3cOsD9DkYkEC3I3QzoAyuuF8dyoPeIi2J2oewtp)
17. [ibm.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFNnQAyOEKdDKzWSPj9JFJytm5Zxeqc80MhZsyPSXxa-L6dc6rCoR57u42RW8qPGjwi43uTDtsYLWtRBWJ2ECrWTlhfZQc0oo3bTv8USDh4IIqcdElYXXfEGqACrYqo)
18. [reddit.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQGRSICV3n7zMO14YFKE3wV7rak1nKeVW-yKm5X8VWOsnF5tHROwCd-oGqrM98N6R7BXpFmHnmwGGolXpm6lb5zbTt2ORGFSkbPOz32eVcH8Ua3s4Nq1KLEHAj6KZ1q1Ro5TL-nmU24bFWncREtX7tzzc_eJ_rDZM7BQLERtdaYz)
19. [alicelabs.ai](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQG6ay0LKwzyGYGU3HX18LsnwHGJzsP5tZkrZtbDoWtN-OwVGLha_aRQgrth5T0ZdVSE8_UIRux3bEOY62NLKfzB3uAPybZR_GNNThw00GBtWLZFLgt4hxgAkejbLFKGsF77RpKFQbyZKCQo2MjOhmvxdaIoyA==)
20. [reddit.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFtvtmsk0By3HFqSpW3UcTXYFl2b6EE7ivWoqp97-GJ_bzadti7kqEaseLplG1sXpT7CmTMQS7t_8LuUIzRik5h5SnizKc2k_YZKdBBihgpPtsC0V2Nkr7OXo65uH_OA1U4mK8u2Sne092xR_hR9WwxqtSnpRpRh-X6B9BURmb0vuQ8rmk7DUURpc8uDie9)
21. [ag2.ai](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQF625cxaYh6_C58UiOjmidQtoqGAv2GfwxlYeD_pwK7fu9C1xgpfWQLnOkEe0iOWwjMR42FchXeGmevswF0mG_e7jS_TzBwyfjMl3yhAGP3CvA8hQuZ7ewPsWrJscGabJhnhV2_t3LykmpzgWXC7R4xEiy5aT2T0vlkV9d0j-g9y6TjFhB3p_s=)
22. [medium.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQF1ZcC5nSN9yJ2nLdVR4cRnyhrdrl3Y08zQxt3qLT56TaEs7UIAoWZW5H3jeoKO2aWlgVYPOLxP9V661hxpSzEQk6aM3nrUUH65cA1LTaE-2kc7IZKYNWMk8lGJOAwCkJdGakbNqFVW3R6ma6DJ4jC7GirqUZH9z8EM6JCXVtukveztfaJkbQ==)
23. [medium.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEjMmcB_ngs7oVN9QfWTJrYAOjU2WRlaNaEI6lU8cwsVYocW93WfULd9oLnv4KwVUOqYb_QMNughW2M6MKM2V47-F6ty1Co4wsa-9ONPYeQmtIy8bJ91gyQ9eIh_qP7zMBR-7qZminjuYL8dJZ3SpV7KywA-sEFkrvTaS5TnlnhPjjAw0an3MX-klLw7gw6pB9sqBw=)
24. [github.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHcchMysnyLgLDiencVP0T4c14QfGTLf0o3SfVMlULS-Z2dad1M1Lbw96bnX0DJZSMY0uClJGn6eFt9SA5YcFhOUJszbfKfXJkBRqOob0bOMzDi5qyrZANTA6Olevn8oQQIhBEI4sVXAqAWBuzDtOy0Ybc1rq4XSUboojRwWbmfd2itHM68G26wFYk=)
25. [ag2.ai](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHQua3s6-ENa9NuZP7Wqfy7z-cA9weQCjoU3KqIvJvTv1QpAC5jLaq4qiKUwtutA1zQmAd1hNYk5_nurLAVSmK3nP-a6ntPi5vybAd-OmSTLMWMvh-kYLZHcGKQcxXjU9161A1ec-IC-LCXm-nVLrld2ELjzJvi)
26. [medium.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQH9AprWfhY5gHFBckPGbccDFq6P2qxieF4uxs-f8s86xZWy6w_2w5GEA8cZKoXbLh5dol89zvrpco683A_9aIdwerge1m0jWBt2krngQwB_mHF1BBqjggn5VpfXl_e2Wllv4aFNVtqDEhBvwXpsx3rvd3NWsKO9kiCkoH-jpVPegTtKeHpidXqDVVkadZ12Efed9l-oZrGy0JMZPMdWfS5nEE67RzI-CRvGaJzjTXM=)
27. [github.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHZx4O1wZ4hyA-kaM3Tn-QMrz79MTcOJR9dzvFBUReN8eKdhN9ZYX_PrGxTgy2u8uVQO-amkRZ5sZcw4SMu2otLB4lD7XGNeMGsBtA4rb4wzOjOGAjCj05N8daXa2lPeWhdsEW0eUR32jh-jmNefojduvPSRzLg4q21gi4XmDc83nze78rQq7YBdYwKcQ==)
28. [venturebeat.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQF0hYxVutcZyrpvLouG5ctAMXVr101gbH_UnaUJPbBGfS8FfP6AtxJPISk4jTldf6-AFItpFy1jA4k3sd8bH2GuEO_z15dN1spnmrahB8zkMKrHiGCc7WWasccw8tvunBLGtn2q9hYv6ehPcSEXwiKXoNwrrDuJO9Y7lb7lWYJsTv-vzoKGvic=)
29. [aihandbook.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEAEyWd_UgNcNI7XXfA2p6wreVmJSch9-hg0eb19dbjX-yf3yfPxvFo9vTnu5je6Fzzzj_yONxfVLM_9beqf2Kbr6bf90wIpE3xrzoLmMiiUQ6265VWUkdXFVfhwJ4EUzy0H13YiPqYQ10rq3d3)
30. [ag2.ai](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHIc2ldY7Re8PisOGa0uqk6zMI-9u0g8Q82lrEWiWFgw-3C1iOg9eo8t6gmRTO07DiC_NohT10N2e0auzJ3Js_iWGAsL2GNd0mUw_HrcW-dmjLt8BINC0SufkaDam5Oy5WPBj2OSoMPXBKYD_ooBnORb73bj9vEPrsw5S17Yg3DQ7lN7WQ=)
31. [google.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQGK86GEAo8B1Jg0pi1fnvai81BSxeLehY1hVE35C17tuA7wbQbTDwgiW2vZniY2VeaqrQyT4i1QSRMMKnRoYR7uVIsrwDH0DmdJgsnOJW4nLu43cYPpwJOe7L8bCnKqyeY_zCnbwrIIPMjzic6dAJIx1I17xmLgiKO2zvNwILy14QEISXyPdoVMvzaRYMJf8T8LKcJIpXBSmbEL)
32. [github.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQH4nnX_7w-_OLIKMhMuSza1Fg7-NIZGdIoWl6vuIeEXqTpRxIzY_w_KsvnSixQepgUrrU5ekSJsNmfWnWbK7-HaW52D_tM6eXVhxuF_3nZcvbW5j_tO1fOepuvdam_ymzn9eEDLbLzps_OIQS4wVnZ9TO5zkpCSNLYXXWUrRhbksRLXSOEm3CZmrV9IbZTUuu12sNNjdWhOpDAJPANZprHXYxA=)
33. [victordibia.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHlPRE6Z0b0ixrOIfpIW2I3NqsdKFgF-2GA24YEsh7Zcp2eUsB5ORJlOaKmC8RVwO64WRKuiEHNZ05k0i3ScqKFXi3TARoRrXI_uZ_K4g_gekWS6uRsPlBK8oqRB2BAmlYgG1fEbVqX8CFraEoleuB9dz1j69Ce19YBMEcu0Mvc3C8=)
34. [amazon.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQG-QrSZF5h48b2XLdlNxxKYltawNaXGLEfd0WfbXvihoOQZpyvKWpmUNTm4cwOau7kDFCJuFAMuWGQWDs3SEF0tY5_lu5mS5DljW-G4XHS-3TlmsqO3U8wY6WK6_C7sCv64avNG4EfJ)
35. [ibm.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHKCs-gusScFwdgQu-sUtMFHEUiTvBto0S_uTqsyCrOB0ut2FsevuoFAfj4RnjUD2DIYmvrR8OHvAdM3OZyY-YtI04-DLpQzC4gl3W_GZvqBp-pkfZoLFlTNJTL6E7SKHDshM5fa7BYQeCx08-GHrO5XhXjWWQ1EfvCk_JPMUoO3RxgOF1v473T5vA=)
36. [contentful.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEA2tkG3_LabPt6WAPv5cOu1XQdSkr3GN8H2DZGPKz32hQc2FT8oFz2gp2JWBgiiAkBW7VTLbhFAt04wiD7_Sk_QLFnEUBJEYBBYV7Sb0qRbiY0ObTtFwQjM5kc2eezuqa63Pth31PUNG8gKHy6iLsl)
37. [wikipedia.org](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQESJasRexn5QEGvlzs_bzcVJ9JaTxTRDwPXIMAc9RPuoQmJNPjg5itBY-JC9rL9fNQXCyNN3vf4WRfAZl4MS7rKaw8FUZb4Qndc-hrPEFVZeo_UA6Dhq_ZAXOzEKg3SAwjLbBIjO0bDQUL1R90SXtCilV9L_p0=)
38. [github.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHo4wA-9fSjl_Pp7QrF5z0DgWUeeWpzZEH2hUtGfNYBA4_BY6s65UVgixv3YW1Jg4w2BzHDT5OC8ftoeooe9HatPM829gyleOCVMTGZhmNq_uX3v-lrLPz3_I8HWfY1QJ_ieZUYdXWyoAVVmUzxR5PcZQ3hW5o2-bP5amfLYNf9KugsBYE=)
39. [github.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFv37Id4ISIr0UqLpZYPLf8BvIuer_hYW1IHjbDaE7xRa9Vzf9ia06u_DA5OytepmnkbbJz-Mb3auVRi4kvPG-axHiEXIG66_Nz_1MYB5cCrlPupqSP8Jb90wybaqIw6XVugzK9uc7yVaPPkwQG6RYb6w-0E_p8vdlMQYXDbdzDDTQbD5glMUR6ZUjcqHl_OrWXKw==)
40. [towardsai.net](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQE4P4tU-yKADYbbozq_6OQMdQX-ACZM4dbmsaaOMHJ8kDjgi4UTIF_clLS8w3z7SK49eXfn1QOaprsxdlKsgEEll30CEurF-U6eJ5NZ1vxPzMEJFUsywQzcANK3FijSaPv_fGOegtSKtl4wH3Qu4E6WxRCkooSD5sqoEbKs3PuUN9tVrmugyc3JmR_e2Q0Q7Dv7qr0E-w==)
41. [plainenglish.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFu7gaoAMvKG2Hc6h5KJwc7kY4NLQV_cWjSxTizAq9_lbYHiudPuWs1aLuS9Iq40qlXO80DsfxevMYp29uP4UCjqdmFsMN9-9HdA-2PTzM9z30ENRyQKwPcU6OsDP94l8YhT-nhad7jnZptvevISshhynNMsinl_jMzm7sfnAzb2_M=)
42. [arxiv.org](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEWENW_VkdVrpX--sR3QeKkykPEgg3nyfMbx6eBhb5EVzvMRd4K06piL5FytkHeN3jDF6N46c4-mvN14Yh0XzqAatoN1R52rh8z7OvjmbuZHLAU9l2KSw==)
43. [azure.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQGuV675wv98O5kvJLn8YmMi8SYCEiy83i5vaPlujm_8rBBBT44JKm4eEiST6ldrILLZ1POzXrUe-a_S5XPwAcEUo00NHnlxNWnfl9-klyoNqQ6X9L-xA4ifnL5OP4bcXSUeXTVD_N0=)
44. [github.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHsnVoST6WrzEhhoGds9yy3ZVezP3bRZa9vGShooGIMer207-xagrIm_wherEn8_G3xxPuPlOXikngLaRzmd-eNl1mmHwX0N5Hk-6JVT4lh-ryu_uGMsRfsANItCnwncHnnILzuKKgReB2lKdbhDfamLnBqZWb4HxBDsmGOTQXa1b9pOhWEurg6)
45. [plainenglish.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEV4Iijya4MimsjdVN1P2B1-wPaaSwJMzpjAUNGagS5FeC7ozsbjEsDFwzupXrIP8RYdSADauTS-VuVU-QeG-hMHB2V9RnhX58zZ5lt0wsZpSLqTCTLSN1purF8xjKx8eHk6lyJ4rxaQ7xv8f5LY6Ae78InHQ5WBD-j1Nc88GWWY4LoYhzs7jn7pO2yBTNjaA430SYMhJGeaO_h_90O)
46. [github.io](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFKdDAZaHCf12e5R6WpXjra3YelaPkAa_euccMN-4tBdN_QTsUSzQzA5jAsYIkyhQB8fxXU0TR-Bliv8cYauB4nr3QPCvFl4ZeA83a-S1__Owris0NSv7gPn_q9zmczc6r64CeZd5HHfPoQsZFiL2789ebIB_q7lUT_1Vlz66g=)
47. [admantium.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQGYf1SBCBQIDqcw2tFMmWWyoyvYRmNjipQ1HGA3CyJSxZfWJgJqkXE2-D0RMrw-t6D6y9DT8Vx4fQmryT4NK_yh6-vNSLrei7ksw0lkXPjkkutxKq_NX-aCT3nQaKX8PKv1r51XJz4=)




---

*Generated by Gemini Deep Research MCP Server*
*Report saved: 2026-05-28T05:44:06.341114*