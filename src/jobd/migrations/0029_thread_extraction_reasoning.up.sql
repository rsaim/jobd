-- The model's own explanation for a thread verdict, when reasoning mode was
-- on. Raw material for `jobd distill`: the "why" compiles into deterministic
-- sender rules without ever re-sending content the model already read.
ALTER TABLE thread_extraction ADD COLUMN reasoning text;
