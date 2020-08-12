import re
from decimal import Decimal
from ast import literal_eval
from datetime import datetime

from SpiffWorkflow.bpmn.parser.util import xpath_eval

from SpiffWorkflow.dmn.specs.model import Decision, DecisionTable, InputEntry, \
    OutputEntry, Input, Output, Rule
from SpiffWorkflow.bpmn.PythonScriptEngine import PythonScriptEngine
from SpiffWorkflow.bpmn.FeelLikeScriptEngine import FeelLikeScriptEngine
from SpiffWorkflow.bpmn.DMNPythonScriptEngine import DMNPythonScriptEngine

DMN_NS = 'http://www.omg.org/spec/DMN/20151101/dmn.xsd'


class DMNParser(object):
    """
    Please note this DMN Parser still needs a lot of work.  A few key areas
    that need to be addressed:
    1. it assumes that only one decision table exists within a decision
    2. it is not always name space aware (I fixed the top level, but could be
       cleaner all the way through.
    """

    DT_FORMAT = '%Y-%m-%dT%H:%M:%S'

    def __init__(self, p, node, svg=None, filename=None, doc_xpath=None):
        """
        Constructor.

        :param p: the owning BpmnParser instance
        :param node: the XML node for the DMN document
        :param svg: the SVG representation of this process as a string
          (optional)
        :param filename: the source BMN filename (optional)
        """
        self.parser = p
        self.node = node
        self.decision = None
        self.svg = svg
        self.filename = filename
        self.doc_xpath = doc_xpath
        self.xpath = xpath_eval(self.node, {'dmn': DMN_NS})
        self.scriptEngine = DMNPythonScriptEngine()

    def parse(self):
        self.decision = self._parse_decision(self.node)

    def get_id(self):
        """
        Returns the process ID
        """
        return self.xpath('dmn:decision[1]')[0].get('id')

    def get_name(self):
        """
        Returns the process name (or ID, if no name is included in the file)
        """
        return self.xpath('dmn:decision[1]')[0].get('name')

    def _parse_decision(self, root):
        decisionElements = list(root)
        if len(decisionElements) == 0:
            raise Exception('No decisions found')

        if len(decisionElements) > 1:
            raise Exception('Multiple decisions found')

        decisionElement = decisionElements[0]
        assert decisionElement.tag.endswith(
            'decision'), 'Element %r is not of type "decision"' % (
            decisionElement.tag)

        decision = Decision(decisionElement.attrib['id'],
                            decisionElement.attrib.get('name', ''))

        # Parse decision tables
        try:
            self._parseDecisionTables(decision, decisionElement)
        except Exception as e:
            raise Exception("Error in Decision '%s': %s" % (decision.name, str(e)))

        return decision

    def _parseDecisionTables(self, decision, decisionElement):
        xpath = xpath_eval(decisionElement, {'dmn': DMN_NS})
        for decisionTableElement in xpath('dmn:decisionTable'):
            decisionTable = DecisionTable(decisionTableElement.attrib['id'],
                                          decisionTableElement.attrib.get(
                                              'name', ''))
            decision.decisionTables.append(decisionTable)

            # parse inputs
            self._parseInputsOutputs(decision, decisionTable,
                                     decisionTableElement)

    def _parseInputsOutputs(self, decision, decisionTable, decisionTableElement):
        for element in decisionTableElement:
            if element.tag.endswith('input'):
                input = self._parseInput(element)
                decisionTable.inputs.append(input)
            elif element.tag.endswith('output'):
                output = self._parseOutput(element)
                decisionTable.outputs.append(output)
            elif element.tag.endswith('rule'):
                rule = self._parseRule(decision, decisionTable, element)
                decisionTable.rules.append(rule)
            else:
                raise Exception(
                    'Unknown type in decision table: %r' % (element.tag))

    def _parseInput(self, inputElement):
        typeRef = None
        xpath = xpath_eval(inputElement, {'dmn': DMN_NS})
        for inputExpression in xpath('dmn:inputExpression'):

            typeRef = inputExpression.attrib.get('typeRef', '')
            scriptEngine = self.scriptEngine
            engine = inputExpression.attrib.get('expressionLanguage')
            if engine == 'feel':
                scriptEngine = FeelLikeScriptEngine()

            expressionNode = inputExpression.find('{' + DMN_NS + '}text')
            if expressionNode is not None:
                expression = expressionNode.text
            else:
                expression = None

        input = Input(inputElement.attrib['id'],
                      inputElement.attrib.get('label', ''),
                      inputElement.attrib.get('name', ''),
                      expression,
                      scriptEngine,
                      typeRef)
        return input

    def _parseOutput(self, outputElement):
        output = Output(outputElement.attrib['id'],
                        outputElement.attrib.get('label', ''),
                        outputElement.attrib.get('name', ''),
                        outputElement.attrib.get('typeRef', ''))
        return output

    def _parseRule(self, decision, decisionTable, ruleElement):
        rule = Rule(ruleElement.attrib['id'])

        inputIdx = 0
        outputIdx = 0
        for child in ruleElement:
            # Load description
            if child.tag.endswith('description'):
                rule.description = child.text

            # Load input entries
            elif child.tag.endswith('inputEntry'):
                inputEntry = self._parseInputOutputElement(decisionTable,
                                                           child,
                                                           InputEntry,
                                                           inputIdx)
                rule.inputEntries.append(inputEntry)
                inputIdx += 1

            # Load output entries
            elif child.tag.endswith('outputEntry'):
                outputEntry = self._parseInputOutputElement(decisionTable,
                                                            child,
                                                            OutputEntry,
                                                            outputIdx)
                rule.outputEntries.append(outputEntry)
                outputIdx += 1

        return rule

    def _parseInputOutputElement(self, decision_table, element, cls, idx):
        inputOrOutput = (
            decision_table.inputs if cls == InputEntry else decision_table.outputs if cls == OutputEntry else None)[
            idx]
        entry = cls(element.attrib['id'], inputOrOutput)
        for child in element:
            if child.tag.endswith('description'):
                entry.description = child.text
            elif child.tag.endswith('text'):
                entry.text = child.text
        if cls == InputEntry:
            entry.lhs.append(entry.text)
        elif cls == OutputEntry:
            if entry.text and entry.text != '':
                py, needsEquals = self.scriptEngine.validateExpression(entry.text)
                if not needsEquals:
                    raise Exception("Malformed Output Expression '%s' " % entry.text)
                else:
                    entry.parsedRef = py
        return entry
