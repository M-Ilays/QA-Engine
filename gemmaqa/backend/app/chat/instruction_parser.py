"""Parse user instructions from chat into structured test configurations."""

import re
from typing import Optional
from pydantic import BaseModel


class TestInstruction(BaseModel):
    """Parsed test instruction from user message."""
    
    intent: str  # test, analyze, report, continue, stop, help
    target_url: Optional[str] = None
    modules: list[str] = []
    features: list[str] = []
    actions: list[str] = []
    scope: str = "focused"  # focused, exploratory, comprehensive
    constraints: dict = {}


class InstructionParser:
    """Parse natural language instructions into test configurations."""
    
    # Greetings / small-talk — these should NEVER trigger a test run
    GREETING_PATTERNS = [
        r"^hi\b", r"^hey\b", r"^hello\b", r"^howdy\b", r"^sup\b",
        r"^good\s+(morning|afternoon|evening|day)\b",
        r"^what'?s\s+up\b", r"^how\s+are\s+you\b", r"^how\s+r\s+u\b",
        r"^yo\b", r"^hiya\b", r"^greetings\b",
        r"^thanks?\b", r"^thank\s+you\b", r"^ok\b", r"^okay\b",
        r"^cool\b", r"^great\b", r"^nice\b", r"^awesome\b",
        r"^bye\b", r"^goodbye\b", r"^see\s+you\b",
        r"^lol\b", r"^haha\b", r"^yes\b", r"^no\b", r"^sure\b",
        r"^what\s+(can|do)\s+you\s+do\b",
        r"^who\s+are\s+you\b",
        r"^help\b",
    ]

    # Intent patterns
    INTENT_PATTERNS = {
        "test": [
            r"test\s+(the\s+)?(.+)",
            r"check\s+(the\s+)?(.+)",
            r"try\s+(to\s+)?(.+)",
            r"verify\s+(the\s+)?(.+)",
            r"validate\s+(the\s+)?(.+)",
            r"run\s+(a\s+)?(qa|test)\b",
            r"qa\s+(the\s+)?(.+)",
            r"scan\s+(the\s+)?(.+)",
            r"explore\s+(the\s+)?(.+)",
            r"audit\s+(the\s+)?(.+)",
        ],
        "analyze": [
            r"analyze\s+(.+)",
            r"examine\s+(.+)",
            r"inspect\s+(.+)",
            r"look\s+at\s+(.+)",
        ],
        "report": [
            r"show\s+(me\s+)?(the\s+)?(.+)",
            r"what\s+(did|have)\s+you\s+(.+)",
            r"give\s+me\s+(.+)",
            r"report\s+(.+)",
            r"summarize\b",
            r"results?\b",
            r"bugs?\s+found\b",
        ],
        "continue": [
            r"continue",
            r"keep\s+going",
            r"proceed",
            r"next",
            r"more",
            r"resume",
        ],
        "stop": [
            r"stop",
            r"pause",
            r"halt",
            r"end",
            r"cancel",
            r"abort",
        ],
        "chat": [
            # Catch-all for questions/statements that aren't test commands
            r"^(what|how|why|when|where|who|can|could|would|should|is|are|do|does)\s+",
        ],
    }
    
    # Module/feature keywords
    MODULE_KEYWORDS = {
        "login": ["login", "sign in", "signin", "authentication", "auth"],
        "signup": ["signup", "sign up", "register", "registration", "create account"],
        "dashboard": ["dashboard", "home page", "main page", "overview"],
        "profile": ["profile", "account", "user settings", "my account"],
        "contacts": ["contact", "contacts", "address book"],
        "forms": ["form", "forms", "input", "submission"],
        "navigation": ["navigation", "menu", "nav", "links"],
        "crud": ["crud", "create", "read", "update", "delete", "add", "edit", "remove"],
        "search": ["search", "find", "filter", "query"],
        "cart": ["cart", "basket", "shopping cart"],
        "checkout": ["checkout", "payment", "purchase"],
    }
    
    # Scope indicators
    SCOPE_KEYWORDS = {
        "focused": ["only", "just", "specific", "targeted", "only the", "just the", "solely", "this feature", "this module"],
        "exploratory": ["explore", "discover", "find out", "investigate", "what else", "look around"],
        "comprehensive": ["everything", "all", "complete", "full", "entire", "whole", "all features", "all pages"],
    }
    
    def parse(self, user_message: str, context: dict = None) -> TestInstruction:
        """Parse user message into structured test instruction."""
        message = user_message.lower().strip()
        
        # Detect intent
        intent = self._detect_intent(message)
        
        # Extract URL if present
        target_url = self._extract_url(user_message)
        
        # Extract modules and features
        modules = self._extract_modules(message)
        features = self._extract_features(message)
        actions = self._extract_actions(message)
        
        # Determine scope
        scope = self._determine_scope(message)
        
        # Extract constraints
        constraints = self._extract_constraints(message)
        
        return TestInstruction(
            intent=intent,
            target_url=target_url or (context or {}).get("target_url"),
            modules=modules,
            features=features,
            actions=actions,
            scope=scope,
            constraints=constraints,
        )
    
    def _detect_intent(self, message: str) -> str:
        """Detect the user's intent.

        Priority order:
        1. Greetings / small-talk  → "chat"  (never triggers a run)
        2. Explicit intent matches → matched intent
        3. URL present + no clear intent → "test"
        4. Everything else → "chat"  (safe default — ask for clarification)
        """
        msg = message.strip().lower()

        # 1. Greetings / small-talk — always safe, never start a run
        for pattern in self.GREETING_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return "chat"

        # 2. Explicit intent match (most specific first)
        for intent, patterns in self.INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, msg, re.IGNORECASE):
                    return intent

        # 3. URL present with no matching intent → treat as test command
        if re.search(r'https?://', msg):
            return "test"

        # 4. Safe default — ask for clarification rather than start a run
        return "chat"
    
    def _extract_url(self, message: str) -> Optional[str]:
        """Extract URL from message."""
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        match = re.search(url_pattern, message)
        return match.group(0) if match else None
    
    def _extract_modules(self, message: str) -> list[str]:
        """Extract module names from the natural-language part of the message.

        URLs are stripped first so that words inside domain names / paths
        (e.g. "contact" in "contact-list.herokuapp.com") are not mistaken
        for requested test modules.
        """
        # Remove URLs before keyword matching
        text_only = re.sub(r'https?://\S+', '', message).strip()
        found_modules = []
        for module, keywords in self.MODULE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_only:
                    found_modules.append(module)
                    break
        return list(set(found_modules))
    
    def _extract_features(self, message: str) -> list[str]:
        """Extract specific features mentioned (quoted words only, URLs stripped)."""
        text_only = re.sub(r'https?://\S+', '', message)
        features = []
        quoted = re.findall(r'"([^"]+)"', text_only)
        features.extend(quoted)
        quoted = re.findall(r"'([^']+)'", text_only)
        features.extend(quoted)
        return features
    
    def _extract_actions(self, message: str) -> list[str]:
        """Extract specific actions to perform."""
        actions = []
        action_keywords = {
            "click": ["click", "press", "tap"],
            "fill": ["fill", "enter", "type", "input"],
            "submit": ["submit", "send"],
            "navigate": ["go to", "navigate", "visit"],
            "verify": ["verify", "check", "validate", "ensure"],
        }
        
        for action, keywords in action_keywords.items():
            for keyword in keywords:
                if keyword in message:
                    actions.append(action)
                    break
        return actions
    
    def _determine_scope(self, message: str) -> str:  # noqa: D401
        # Strip URLs so path segments like "/complete-list" don't affect scope
        message = re.sub(r'https?://\S+', '', message).strip()
        """Determine the test scope.
        
        Priority: comprehensive > exploratory > focused.
        Default is 'focused' whenever the user names a specific module
        without asking for "everything" — this prevents scope creep.
        """
        # Comprehensive wins if explicitly requested
        for keyword in self.SCOPE_KEYWORDS["comprehensive"]:
            if keyword in message:
                return "comprehensive"
        
        # Exploratory if investigation language present
        for keyword in self.SCOPE_KEYWORDS["exploratory"]:
            if keyword in message:
                return "exploratory"
        
        # Focused if explicit focusing words present
        for keyword in self.SCOPE_KEYWORDS["focused"]:
            if keyword in message:
                return "focused"
        
        # Default: if the message names specific modules, treat as focused
        # to avoid unintended scope creep
        return "focused"
    
    def _extract_constraints(self, message: str) -> dict:
        """Extract constraints like time limits, action limits, etc."""
        constraints = {}
        
        # Max actions
        action_match = re.search(r'(\d+)\s+actions?', message)
        if action_match:
            constraints["max_actions"] = int(action_match.group(1))
        
        # Time limit
        time_match = re.search(r'(\d+)\s+(minute|min|second|sec)s?', message)
        if time_match:
            value = int(time_match.group(1))
            unit = time_match.group(2)
            if unit.startswith('min'):
                constraints["max_runtime_seconds"] = value * 60
            else:
                constraints["max_runtime_seconds"] = value
        
        # Don't create test data
        if any(phrase in message for phrase in ["don't create", "no test data", "read only"]):
            constraints["allow_safe_test_data_creation"] = False
        
        # Allow destructive
        if any(phrase in message for phrase in ["allow delete", "destructive", "cleanup"]):
            constraints["allow_destructive_actions"] = True
        
        return constraints
    
    def generate_config_from_instruction(
        self,
        instruction: TestInstruction,
        context: dict = None
    ) -> dict:
        """Generate a RunConfiguration from parsed instruction."""
        # RunConfiguration is only used for type hints here; just return a plain dict
        
        module_count = max(1, len(instruction.modules) + len(instruction.features))
        default_actions = {
            "focused":       50 * module_count,
            "exploratory":   150,
            "comprehensive": 300,
        }.get(instruction.scope, 50)
        
        config = {
            "max_actions": instruction.constraints.get("max_actions", default_actions),
            "max_pages": max(5, module_count * 5) if instruction.scope == "focused" else 50,
            "allow_safe_test_data_creation": instruction.constraints.get("allow_safe_test_data_creation", True),
            "allow_destructive_actions": instruction.constraints.get("allow_destructive_actions", False),
        }
        
        # Add module/feature filters
        if instruction.modules or instruction.features:
            config["focus_modules"] = instruction.modules
            config["focus_features"] = instruction.features
        
        # Add action constraints
        if instruction.actions:
            config["allowed_actions"] = instruction.actions
        
        # Add runtime constraints
        if "max_runtime_seconds" in instruction.constraints:
            config["max_runtime_seconds"] = instruction.constraints["max_runtime_seconds"]
        
        return config
